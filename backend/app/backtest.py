"""Replay real Coinbase history through the exact same engine (walk-forward sanity check).

    python -m app.backtest --days 14
    python -m app.backtest --days 14 --fee-mode auto

Each 5-minute candle becomes 4 ticks (open, low/high, high/low, close), so fills and
stops are evaluated conservatively intrabar. Results are NOT a promise of future
performance - they're how we catch broken logic and over-optimistic assumptions.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from collections import Counter
from pathlib import Path

import httpx

from .arena import Arena
from .config import SYMBOLS, TIMEFRAMES
from .market.candles import Candle
from .market.coinbase import HEADERS, fetch_range
from .market.hub import MarketHub

CACHE = Path(__file__).resolve().parents[1] / ".bt-cache"


async def _get(client, sym, tf, start, end) -> list[Candle]:
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"{sym}-{tf}-{int(start)}-{int(end)}.json"
    if f.exists():
        return [Candle(*x) for x in json.loads(f.read_text())]
    cs = await fetch_range(client, sym, tf, start, end)
    f.write_text(json.dumps([[c.t, c.o, c.h, c.l, c.c, c.v] for c in cs]))
    return cs


async def load(days: float, symbols: list[str]):
    end = int(time.time() // 3600 * 3600)
    start = end - int(days * 86400)
    seeds: dict[str, dict[int, list[Candle]]] = {}
    replay: dict[str, list[Candle]] = {}
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        for s in symbols:
            seeds[s] = {}
            for tf in TIMEFRAMES:
                seeds[s][tf] = await _get(client, s, tf, start - 300 * tf, start)
            replay[s] = await _get(client, s, 300, start, end)
            print(f"  loaded {s}: {len(replay[s])} replay candles", flush=True)
    return start, end, seeds, replay


def run(days: float, fee_mode: str | None, symbols: list[str], db: str | None = None,
        stop_when_open: int = 0) -> dict:
    print(f"Fetching {days} days of Coinbase history...", flush=True)
    start, end, seeds, replay = asyncio.run(load(days, symbols))
    hub = MarketHub(SYMBOLS)
    hub.use_wall_clock = False
    hub.clock = start
    for s in symbols:
        for tf, cs in seeds[s].items():
            hub.series[s][tf].seed(cs, start)
        last = seeds[s][300][-1]
        hub.on_trade(s, last.c, 0.0, start - 1)
    store = None
    if db:  # write the replay into a database (useful for demoing the UI with history)
        from .storage import Store
        store = Store(Path(db))
        store.clear()
    arena = Arena(hub, store, latency=(0.0, 0.0))
    if fee_mode:
        arena.fees.mode = fee_mode
    arena.start(start)

    by_t: dict[int, list[tuple[str, Candle]]] = {}
    for s, cs in replay.items():
        for c in cs:
            by_t.setdefault(c.t, []).append((s, c))
    kinds: Counter = Counter()
    t0 = time.time()
    for t in sorted(by_t):
        rows = by_t[t]
        for k, off in enumerate((1, 100, 200, 299)):
            for s, c in rows:
                up = c.c >= c.o
                px = (c.o, c.l if up else c.h, c.h if up else c.l, c.c)[k]
                hub.on_trade(s, px, c.v / 4, t + off)
            now = t + off
            hub.clock = now
            arena.step(now)
            evs, _ = arena.drain()
            kinds.update(e["kind"] for e in evs)
        if stop_when_open and t > start + 86400 and sum(
                1 for b in arena.bots for p in b.positions.values() if p.status == "open") >= stop_when_open:
            break
    elapsed = time.time() - t0
    if store:
        arena.save()

    out = {"days": days, "fee_mode": arena.fees.mode, "fee_tier_end": arena.fees.tier(hub.now()).name,
           "elapsed_s": round(elapsed, 1), "events": dict(kinds), "bots": {}}
    for b in arena.bots:
        sm = b.summary_json(hub.now())
        trades = list(b.closed)
        reasons = Counter(t["reason"] for t in trades)
        strat = Counter(t["strategy"] for t in trades)
        out["bots"][b.p.name] = {
            "equity": round(sm["equity"], 2), "return_pct": round(sm["total_return"] * 100, 2),
            "trades": sm["all"]["trades"], "win_rate": sm["all"]["win_rate"],
            "profit_factor": sm["all"]["profit_factor"], "fees": round(sm["all"]["fees"], 2),
            "max_dd_pct": round(sm["all"]["max_dd"] * 100, 2), "est_tax": round(sm["tax"]["total"], 2),
            "avg_r": round(sum(t["r"] for t in trades) / len(trades), 2) if trades else None,
            "exit_reasons": dict(reasons), "strategies": dict(strat), "open_now": sm["open"],
            "gross_pnl": round(sum(t["gross"] for t in trades), 2),
            "by_strategy": {k: {"n": sum(1 for t in trades if t["strategy"] == k),
                                "net": round(sum(t["net"] for t in trades if t["strategy"] == k), 2),
                                "gross": round(sum(t["gross"] for t in trades if t["strategy"] == k), 2)}
                            for k in strat},
        }
    return out


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--fee-mode", default=None, help='"auto" or a tier name, e.g. "Advanced 2"')
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--stop-when-open", type=int, default=0, help="stop the replay once N positions are open")
    ap.add_argument("--db", default=None, help="write the replay into this sqlite file")
    ap.add_argument("--json", action="store_true", help="print raw JSON results")
    ap.add_argument("--override", default="", help='JSON, e.g. {"high": {"signal_tf": 900}}')
    a = ap.parse_args()
    if a.override:
        from dataclasses import replace

        from . import config
        ov = json.loads(a.override)
        for i, p in enumerate(config.PROFILES):
            if p.id in ov:
                config.PROFILES[i] = replace(p, **{k: tuple(v) if isinstance(v, list) else v
                                                   for k, v in ov[p.id].items()})
    res = run(a.days, a.fee_mode, a.symbols.split(","), a.db, a.stop_when_open)
    if a.json:
        print(json.dumps(res, indent=2))
        return
    print(f"\n{a.days:g}-day replay · fee tier {res['fee_tier_end']} · {res['elapsed_s']}s\n")
    print(f"{'BOT':<11}{'RETURN':>9}{'TRADES':>8}{'WIN%':>7}{'PF':>6}{'GROSS':>11}{'FEES':>10}{'MAX DD':>9}")
    for name, v in res["bots"].items():
        wr = f"{v['win_rate'] * 100:.0f}" if v["win_rate"] is not None else "-"
        pf = f"{v['profit_factor']:.2f}" if v["profit_factor"] is not None else "-"
        print(f"{name:<11}{v['return_pct']:>8.2f}%{v['trades']:>8}{wr:>7}{pf:>6}"
              f"{v['gross_pnl']:>11,.2f}{v['fees']:>10,.2f}{v['max_dd_pct']:>8.2f}%")
    print("\nGROSS = P&L before fees. The gap between GROSS and RETURN is what the exchange kept.")


if __name__ == "__main__":
    main()
