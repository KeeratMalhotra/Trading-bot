"""FastAPI app: REST + WebSocket API, engine loop, static dashboard."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .accounting.tax import STATE_RATES, TaxSettings
from .arena import Arena
from .config import (ADMIN_TOKEN, COINBASE_FEE_TIERS, DB_PATH, MARKET_SOURCE, STATIC_DIR, SYMBOLS,
                     TIMEFRAMES)
from .market import coinbase, simulated
from .market.hub import MarketHub
from .ml.oracle import OracleService
from .storage import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def dumps(o) -> str:
    return json.dumps(_clean(o), separators=(",", ":"))


class State:
    hub: MarketHub
    arena: Arena
    store: Store
    clients: dict[WebSocket, dict]
    stop: asyncio.Event
    tasks: list[asyncio.Task]
    oracle: OracleService


S = State()


async def _start_market(hub: MarketHub) -> None:
    if MARKET_SOURCE != "sim":
        try:
            await asyncio.wait_for(coinbase.load_history(hub), timeout=120)
            S.tasks.append(asyncio.create_task(coinbase.run_ticker(hub, S.stop)))
            S.tasks.append(asyncio.create_task(coinbase.resync_loop(hub, S.stop)))
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Coinbase history failed (%s)", e)
            if MARKET_SOURCE == "coinbase":
                raise
    log.warning("Using SIMULATED market data")
    simulated.seed_history(hub, time.time())
    S.tasks.append(asyncio.create_task(simulated.run_sim(hub, S.stop)))


async def engine_loop() -> None:
    while not S.stop.is_set():
        try:
            S.arena.step(time.time())
        except Exception:  # noqa: BLE001
            log.exception("engine step failed")
        await asyncio.sleep(0.25)


async def broadcast_loop() -> None:
    tick = 0
    while not S.stop.is_set():
        await asyncio.sleep(0.5)
        tick += 1
        if not S.clients:
            S.arena.drain()
            continue
        now = time.time()
        events, trades = S.arena.drain()
        msgs = [dumps({"t": "live", **S.arena.live(now)})]
        if events:
            msgs.append(dumps({"t": "events", "items": events}))
        if trades:
            msgs.append(dumps({"t": "trades", "items": [{k: v for k, v in x.items() if k != "timeline"} for x in trades]}))
        if tick % 60 == 0:
            msgs.append(dumps({"t": "equity", "data": S.arena.equity_json()}))
            msgs.append(dumps({"t": "meta", "data": S.arena.meta(now)}))
        for ws, sub in list(S.clients.items()):
            try:
                for m in msgs:
                    await ws.send_text(m)
                sym, tf = sub.get("symbol"), sub.get("tf")
                if sym in S.hub.series and tf in S.hub.series[sym]:
                    f = S.hub.series[sym][tf].forming
                    if f:
                        await ws.send_text(dumps({"t": "candle", "symbol": sym, "tf": tf, "c": f.to_json()}))
            except Exception:  # noqa: BLE001
                S.clients.pop(ws, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    S.stop = asyncio.Event()
    S.tasks = []
    S.clients = {}
    S.store = Store(DB_PATH)
    S.hub = MarketHub(SYMBOLS)
    await _start_market(S.hub)
    S.oracle = OracleService()
    S.tasks.append(asyncio.create_task(S.oracle.run(S.stop)))
    S.arena = Arena(S.hub, S.store, oracle=S.oracle)
    now = time.time()
    restored = S.arena.load(now)
    S.arena.start(now)
    if restored:
        S.arena.system("Bots back online", "State restored. Open positions re-armed with their stops and targets.")
    else:
        S.arena.system("Bot Battle started",
                       f"Three bots, {S.arena.bots[0].portfolio.cash:,.0f} demo dollars each, live market prices. "
                       "Every thought and every trade shows up right here.", "good")
    S.tasks.append(asyncio.create_task(engine_loop()))
    S.tasks.append(asyncio.create_task(broadcast_loop()))
    yield
    S.stop.set()
    S.arena.save()
    for t in S.tasks:
        t.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await t


app = FastAPI(title="Bot Battle", lifespan=lifespan)


def require_admin(token: str | None) -> None:
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")


# ------------------------------------------------------------------ REST
@app.get("/api/health")
def health():
    return {"ok": True, "market": S.hub.status.__dict__}


@app.get("/api/snapshot")
def snapshot():
    return Response(dumps(S.arena.snapshot(time.time())), media_type="application/json")


@app.get("/api/candles")
def candles(symbol: str = Query(...), tf: int = Query(300), limit: int = Query(300, le=600)):
    if symbol not in S.hub.series or tf not in TIMEFRAMES:
        raise HTTPException(404, "unknown symbol/timeframe")
    return [c.to_json() for c in S.hub.series[symbol][tf].recent(limit)]


@app.get("/api/trades")
def trades(bot: str | None = None, limit: int = 200):
    return Response(dumps(S.store.trades(bot, limit)), media_type="application/json")


@app.get("/api/export/{bot_id}.csv")
def export_csv(bot_id: str):
    """Form 8949 / 1099-DA style disposal report (FIFO lots)."""
    bot = S.arena.by_id.get(bot_id)
    if not bot:
        raise HTTPException(404)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Description", "Date acquired", "Date sold", "Proceeds (USD)", "Cost basis (USD)",
                "Gain/loss (USD)", "Term", "Trade ID"])
    iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")  # noqa: E731
    for d in bot.portfolio.disposals:
        w.writerow([f"{d.qty:.8f} {d.symbol.split('-')[0]}", iso(d.acquired_ts), iso(d.sold_ts),
                    f"{d.proceeds:.2f}", f"{d.basis:.2f}", f"{d.gain:.2f}",
                    "Short-term" if d.term == "short" else "Long-term", d.trade_id])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{bot.p.name.lower()}-8949-demo.csv"'})


class SettingsIn(BaseModel):
    filing_status: str | None = None
    other_income: float | None = None
    state: str | None = None
    state_rate_override: float | None = None
    fee_mode: str | None = None


@app.post("/api/settings")
def update_settings(body: SettingsIn, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    t = S.arena.tax
    if body.filing_status in ("single", "mfj"):
        t.filing_status = body.filing_status
    if body.other_income is not None and 0 <= body.other_income <= 10_000_000:
        t.other_income = body.other_income
    if body.state and body.state in STATE_RATES:
        t.state = body.state
        t.state_rate_override = None
    if body.state_rate_override is not None and 0 <= body.state_rate_override <= 0.2:
        t.state_rate_override = body.state_rate_override
    if body.fee_mode and (body.fee_mode == "auto" or body.fee_mode in {x.name for x in COINBASE_FEE_TIERS}):
        S.arena.fees.mode = body.fee_mode
    S.arena.save()
    return S.arena.meta(time.time())


@app.post("/api/reset")
def reset(x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    S.arena.reset(time.time())
    return {"ok": True}


@app.post("/api/bots/{bot_id}/{action}")
def bot_action(bot_id: str, action: str, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    bot = S.arena.by_id.get(bot_id)
    if not bot or action not in ("pause", "resume"):
        raise HTTPException(404)
    bot.paused = action == "pause"
    bot.status_text = "Paused" if bot.paused else "Resuming"
    bot.emit("system", f"{bot.p.name} {'paused' if bot.paused else 'resumed'}",
             "Manual control from the operator." + (" Open trades stay protected by their stops." if bot.paused else ""),
             level="warn" if bot.paused else "good")
    return {"ok": True}


@app.post("/api/positions/{pos_id}/close")
def close_position(pos_id: str, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    for b in S.arena.bots:
        if b.close_manual(pos_id, time.time()):
            return {"ok": True}
    raise HTTPException(404)


@app.get("/api/auth")
def auth_check(x_admin_token: str | None = Header(None)):
    return {"required": bool(ADMIN_TOKEN), "ok": not ADMIN_TOKEN or x_admin_token == ADMIN_TOKEN}


# ------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    await ws.send_text(dumps({"t": "snapshot", **S.arena.snapshot(time.time())}))
    S.clients[ws] = {}
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("op") == "sub":
                S.clients[ws] = {"symbol": msg.get("symbol"), "tf": int(msg.get("tf", 300))}
    except (WebSocketDisconnect, Exception):  # noqa: BLE001
        pass
    finally:
        S.clients.pop(ws, None)


# ------------------------------------------------------------ dashboard
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = (STATIC_DIR / path).resolve()
        if path and f.is_file() and f.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(f)
        return FileResponse(STATIC_DIR / "index.html")
else:
    @app.get("/")
    def no_ui():
        return JSONResponse({"error": "frontend not built", "hint": "cd frontend && npm install && npm run build"})


__all__ = ["app", "TaxSettings"]
