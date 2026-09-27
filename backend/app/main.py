"""QUORUM server: live market data, forecasting, news, the trading team, REST + WebSocket API.

    uvicorn app.main:app --port 8000

"""
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
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .accounting.tax import STATE_RATES
from .config import (ADMIN_TOKEN, DATA_DIR, DB_PATH, FEED_STALE_S, MARKET_SOURCE, STATIC_DIR, SYMBOLS, TEAM_BALANCE,
                     TIMEFRAMES)
from .market import coinbase, simulated
from .market.hub import MarketHub
from .ml.altdata import AltData
from .ml.data import HistoryStore
from .storage import Store
from .team.engine import AGENT_INFO, AGENTS, Quorum
from .team.forecaster import LiveForecaster
from .team.funding import CoinbaseFunding
from .team.news import NewsDesk

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("app")
ET = ZoneInfo("America/New_York")


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):  # numpy scalar
        return _clean(o.item())
    return o


def dumps(o) -> str:
    return json.dumps(_clean(o), separators=(",", ":"))


class S:
    hub: MarketHub
    store: Store
    history: HistoryStore
    alt: AltData
    fc: LiveForecaster
    news: NewsDesk
    cbf: CoinbaseFunding
    q: Quorum
    clients: dict
    stop: asyncio.Event
    tasks: list
    feed_seen: bool = False
    feed_down_since: float = 0.0


async def market_boot() -> None:
    """Coinbase candle history, then the live ticker. Retries until Coinbase answers and never swaps
    in simulated prices (they would end up in the paper account). MARKET_SOURCE=sim is for demos only."""
    hub = S.hub
    if MARKET_SOURCE == "sim":
        log.warning("MARKET_SOURCE=sim: using SIMULATED market data")
        simulated.seed_history(hub, time.time())
        S.tasks.append(asyncio.create_task(simulated.run_sim(hub, S.stop)))
        return
    delay = 5.0
    while not S.stop.is_set():
        try:
            await asyncio.wait_for(coinbase.load_history(hub), timeout=120)
            break
        except Exception as e:  # noqa: BLE001
            hub.status.message = "Waiting for Coinbase market data"
            log.warning("Coinbase history failed (%s); retrying in %.0fs", e, delay)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(S.stop.wait(), timeout=delay)
            delay = min(delay * 2, 120)
    if not S.stop.is_set():
        S.tasks.append(asyncio.create_task(coinbase.run_ticker(hub, S.stop)))
        S.tasks.append(asyncio.create_task(coinbase.resync_loop(hub, S.stop)))


def prices() -> dict:
    return {s: (q.bid, q.ask, q.price) for s, q in S.hub.quotes.items() if q.price > 0}


def feed_ok() -> bool:
    st = S.hub.status
    return st.connected and time.time() - st.last_msg < FEED_STALE_S


# ------------------------------------------------------------------ loops
def _feed_guard() -> bool:
    """Trade only on live prices. If Coinbase goes quiet, pause (and say so) until it's back."""
    ok = feed_ok()
    if ok:
        if S.feed_down_since:
            mins = (time.time() - S.feed_down_since) / 60
            S.q.emit("system", "DESK", "Market data restored",
                     f"Live Coinbase prices are back after {mins:.0f} min. Trading resumed.", "good")
            S.feed_down_since = 0.0
        S.feed_seen = True
    elif S.feed_seen and not S.feed_down_since:
        S.feed_down_since = time.time()
        S.q.emit("system", "DESK", "Market data interrupted",
                 "No live prices from Coinbase, so trading is paused. Positions are left as they are.", "warn")
    return ok


async def engine_loop() -> None:
    last_save = 0.0
    while not S.stop.is_set():
        try:
            if _feed_guard():
                S.q.step(time.time())
            evs = S.q.drain()
            if evs:
                S.store.add_events(evs)
                S.pending_events.extend(evs)
            if time.time() - last_save > 10:
                last_save = time.time()
                S.store.kv_set("quorum", S.q.dump())
        except Exception:  # noqa: BLE001
            log.exception("engine step failed")
        await asyncio.sleep(0.5)


async def desk_loop() -> None:
    """After each UTC daily close (once the day's last hourly candle is in), recompute the desk."""
    while not S.stop.is_set():
        try:
            if S.fc.ready:
                arr = S.history.raw.get("BTC-USD")
                have = int((arr[-1, 0] + 3600) // 86400) - 1 if arr is not None and len(arr) else 0
                cur = S.q.pending_desk
                cur_day = int(cur.d.days[-1]) if cur is not None and len(cur.d.days) else -1
                last_bar = S.fc.latest_bar() or 0
                forecasts_in = last_bar >= (have + 1) * 86400 - 3600
                stale = time.time() - (have + 1) * 86400 > 900
                if have > cur_day and (forecasts_in or stale):
                    t0 = time.time()
                    st = await asyncio.to_thread(S.q.compute_desk)
                    if st is not None:
                        S.q.pending_desk = st
                        log.info("desk computed for %s in %.1fs",
                                 time.strftime("%Y-%m-%d", time.gmtime(int(st.d.days[-1]) * 86400)), time.time() - t0)
        except Exception:  # noqa: BLE001
            log.exception("desk loop failed")
        try:
            await asyncio.wait_for(S.stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


async def news_bridge() -> None:
    while not S.stop.is_set():
        for ev in S.news.new_events:
            if ev["kind"] == "news_veto":
                it = ev["item"]
                coins = ", ".join(c.split("-")[0] for c in it["coins"])
                S.q.emit("news", "NEWS", f"Veto: no new longs in {coins} for 48h", f"{it['source']}: {it['title']}",
                         "warn", it["coins"][0] if it["coins"] else None, {"link": it["link"]})
            elif ev["kind"] == "news_risk":
                it = ev["item"]
                S.q.emit("news", "NEWS", "Headline risk: new longs paused for 12h", f"{it['source']}: {it['title']}",
                         "warn", None, {"link": it["link"]})
        S.news.new_events = []
        await asyncio.sleep(2)


async def broadcast_loop() -> None:
    tick = 0
    while not S.stop.is_set():
        await asyncio.sleep(1.0)
        tick += 1
        evs, S.pending_events = S.pending_events, []
        if not S.clients:
            continue
        now = time.time()
        msgs = [dumps({"t": "live", "data": live_payload(now)})]
        if evs:
            msgs.append(dumps({"t": "events", "items": evs}))
        if tick % 15 == 0:
            msgs.append(dumps({"t": "oracle", "data": S.fc.to_json()}))
            msgs.append(dumps({"t": "news", "data": S.news.to_json(now)}))
        if tick % 60 == 0:
            msgs.append(dumps({"t": "history", "data": history_payload(now)}))
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


# ------------------------------------------------------------------ payloads
def live_payload(now: float) -> dict:
    d = S.q.summary(now)
    d["quotes"] = [{"s": s, "p": q.price, "chg": (q.price / q.open_24h - 1) if q.open_24h else 0}
                   for s, q in S.hub.quotes.items() if q.price > 0]
    d["market"] = {"source": S.hub.status.source, "connected": S.hub.status.connected,
                   "message": S.hub.status.message}
    d["engine"] = {"forecaster": S.fc.status_text(), "ready": S.fc.ready,
                   "desk_ready": S.q.pending_desk is not None or S.q.last_day > 0,
                   "paused": not feed_ok()}
    return d


def history_payload(now: float) -> dict:
    since = now - 400 * 86400
    rows = S.store.equity(since)
    team = rows.get("team", [])
    btc = rows.get("BTC", [])
    # thin to <= ~2000 points
    def thin(xs, n=2000):
        step = max(1, len(xs) // n)
        out = xs[::step]
        if xs and out[-1] != xs[-1]:
            out.append(xs[-1])
        return [[int(t), round(v, 2)] for t, v in out]
    # daily / monthly P&L from the engine's own period marks (same numbers as "Today" and "Month to date")
    eq = S.q.equity()
    ds = sorted(S.q.day_start.items())
    day_pnl = {d0: v1 - v0 for (d0, v0), (_, v1) in zip(ds, ds[1:], strict=False)}
    if ds:
        day_pnl[ds[-1][0]] = eq - ds[-1][1]
    ms = sorted(S.q.month_start.items())
    mret = {m0: {"pnl": v1 - v0, "ret": v1 / v0 - 1} for (m0, v0), (_, v1) in zip(ms, ms[1:], strict=False) if v0}
    if ms and ms[-1][1]:
        mret[ms[-1][0]] = {"pnl": eq - ms[-1][1], "ret": eq / ms[-1][1] - 1}
    return {"equity": thin(team), "btc": thin(btc), "agents": {a: thin(rows.get(a, [])) for a in AGENTS},
            "mix": thin(rows.get("mix", [])), "day_pnl": day_pnl, "months": mret}


def _start_mix() -> None:
    """Accounts that were already running when the what-if line was added: start it at the account's
    first saved sample and rebuild its past from the saved account/BTC history, so the chart compares
    like with like. New accounts start it with the first live BTC price."""
    q = S.q
    if q.mix is not None or q.mix_share <= 0:
        return
    rows = S.store.equity(0)
    team, btc = rows.get("team", []), dict(rows.get("BTC", []))
    pts = [(t, eq, btc[t]) for t, eq in team if btc.get(t, 0) > 0]
    if not pts:
        return
    q.init_mix(pts[0][2], pts[0][0])
    S.store.add_equity([("mix", t, q.mix_at(t, eq, p)) for t, eq, p in pts])
    log.info("what-if portfolio rebuilt from %d saved samples", len(pts))


def snapshot(now: float) -> dict:
    return {"live": live_payload(now), "events": S.store.events(250), "history": history_payload(now),
            "oracle": S.fc.to_json(), "news": S.news.to_json(now), "fills": list(S.q.fills)[:120],
            "meta": {"agents": AGENT_INFO, "symbols": SYMBOLS, "started": S.q.started, "deposits": S.q.book.deposits,
                     "tax": S.q.tax.to_json(), "states": [{"code": k, "name": v[0], "rate": v[1]} for k, v in STATE_RATES.items()],
                     "account": "QRM-0001"}}


# ------------------------------------------------------------------ app
@asynccontextmanager
async def lifespan(app: FastAPI):
    S.stop = asyncio.Event()
    S.tasks = []
    S.clients = {}
    S.pending_events = []
    S.store = Store(DB_PATH)
    S.hub = MarketHub(SYMBOLS)
    S.history = HistoryStore(DATA_DIR / "history")
    S.alt = AltData(DATA_DIR / "altdata")
    S.alt.load()
    S.fc = LiveForecaster(S.history, S.alt, DATA_DIR)
    S.news = NewsDesk()
    S.cbf = CoinbaseFunding(S.alt, SYMBOLS)
    S.q = Quorum(SYMBOLS, TEAM_BALANCE, prices, None, S.fc, S.news, S.store, S.history, S.alt)
    saved = S.store.kv_get("quorum")
    if saved:
        S.q.load(saved)
        _start_mix()
        S.q.emit("system", "DESK", "Systems online", "State restored. Positions, stops and targets re-armed.")
    else:
        S.q.emit("system", "DESK", "Account opened", f"QUORUM starts with {TEAM_BALANCE:,.0f} USD. "
                 "Loading market history and training forecast models before the first trade.")
    for coro in (market_boot(), S.fc.run(S.stop), S.news.run(S.stop), S.cbf.run(S.stop), engine_loop(), desk_loop(),
                 news_bridge(), broadcast_loop()):
        S.tasks.append(asyncio.create_task(coro))
    yield
    S.stop.set()
    S.store.kv_set("quorum", S.q.dump())
    for t in S.tasks:
        t.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await t


app = FastAPI(title="QUORUM", lifespan=lifespan)


def require_admin(token: str | None) -> None:
    if ADMIN_TOKEN and token != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")


@app.get("/api/health")
def health():
    return {"ok": True, "market": S.hub.status.__dict__, "feed_ok": feed_ok(), "forecaster": S.fc.status_text(),
            "desk_ready": S.q.pending_desk is not None or S.q.last_day > 0, "news": S.news.status,
            "coinbase_funding": S.cbf.to_json(), "equity": S.q.equity(),
            "positions": len(S.q.book.perps)}


@app.get("/api/backtest")
def api_backtest():
    """Hypothetical long backtest for the Backtest tab: BTC held alone vs the what-if split (MIX_* settings)."""
    from .team.mixbacktest import dashboard
    return Response(dumps(dashboard(S.q.mix_share, S.q.mix_months)), media_type="application/json")


@app.get("/api/snapshot")
def api_snapshot():
    return Response(dumps(snapshot(time.time())), media_type="application/json")


@app.get("/api/candles")
def candles(symbol: str = Query(...), tf: int = Query(3600), limit: int = Query(300, le=600)):
    if symbol not in S.hub.series or tf not in TIMEFRAMES:
        raise HTTPException(404, "unknown symbol/timeframe")
    return [c.to_json() for c in S.hub.series[symbol][tf].recent(limit)]


class SettingsIn(BaseModel):
    filing_status: str | None = None
    other_income: float | None = None
    state: str | None = None


@app.post("/api/settings")
def settings(body: SettingsIn, x_admin_token: str | None = Header(None)):
    require_admin(x_admin_token)
    t = S.q.tax
    if body.filing_status in ("single", "mfj"):
        t.filing_status = body.filing_status
    if body.other_income is not None and 0 <= body.other_income <= 10_000_000:
        t.other_income = body.other_income
    if body.state in STATE_RATES:
        t.state = body.state
    return t.to_json()


@app.post("/api/jerseys")
def jerseys(body: dict[str, float], x_admin_token: str | None = Header(None)):
    """This week's fan split for the team jerseys, e.g. from a Twitch/YouTube poll: {"ATLAS": 45, "ORACLE": 35, "NOVA": 20}."""
    require_admin(x_admin_token)
    fans = S.q.show.set_fans(body)
    if fans is None:
        raise HTTPException(409, "the week hasn't started yet")
    return {"week": S.q.show.week["id"], "fans": fans}


@app.get("/api/auth")
def auth(x_admin_token: str | None = Header(None)):
    return {"required": bool(ADMIN_TOKEN), "ok": not ADMIN_TOKEN or x_admin_token == ADMIN_TOKEN}


@app.get("/api/export/8949.csv")
def export_8949():
    """Spot disposals (Form 8949) + a Section 1256 summary line for the perpetual-style futures."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Description", "Date acquired", "Date sold", "Proceeds", "Cost basis", "Gain/loss", "Term"])
    iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    for d in S.q.book.spot.disposals:
        w.writerow([f"{d.qty:.8f} {d.symbol.split('-')[0]}", iso(d.acquired_ts), iso(d.sold_ts), f"{d.proceeds:.2f}",
                    f"{d.basis:.2f}", f"{d.gain:.2f}", "Short-term" if d.term == "short" else "Long-term"])
    w.writerow([])
    w.writerow(["Form 6781 (Section 1256) - regulated futures, marked to market", "Year", "Realized incl. funding & fees"])
    for y, v in sorted(S.q.book.perp_realized.items()):
        w.writerow(["Coinbase Derivatives perpetual-style futures", y, f"{v:.2f}"])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="quorum-tax-report.csv"'})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    await ws.send_text(dumps({"t": "snapshot", **snapshot(time.time())}))
    S.clients[ws] = {}
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("op") == "sub":
                S.clients[ws] = {"symbol": msg.get("symbol"), "tf": int(msg.get("tf", 3600))}
    except (WebSocketDisconnect, Exception):  # noqa: BLE001
        pass
    finally:
        S.clients.pop(ws, None)


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
