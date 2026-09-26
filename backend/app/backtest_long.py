"""Multi-year replay of the REAL trading engine on hourly Coinbase history.

    python -m app.backtest_long --period dev        # 2022-01-01 .. 2026-03-26
    python -m app.backtest_long --period holdout    # 2026-03-26 .. now (untouched while designing)

Every hourly candle becomes 4 ticks (open, low/high, high/low, close) fed through the same
MarketHub -> bots -> paper exchange -> tax pipeline used live, with Coinbase fee tiers.
ORACLE uses only walk-forward forecasts (each made by a model trained on earlier data).
Bots that need sub-hour candles (TACTICIAN, BERSERKER) can't run on hourly data and are
excluded - see app.backtest for their 5-minute replay.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from collections import Counter

import numpy as np

from .arena import Arena
from .config import DATA_DIR, SYMBOLS
from .market.candles import Candle
from .market.hub import MarketHub
from .ml.data import TF, HistoryStore
from .ml.oracle import OracleReplay
from .ml.research import HOLDOUT_START, OOS_START

BOTS = ["oracle", "nomad", "low", "hodl"]


def aggregate(arr: np.ndarray, tf: int) -> list[Candle]:
    if not len(arr):
        return []
    b = (arr[:, 0] // tf).astype(np.int64)
    keys, first = np.unique(b, return_index=True)
    last = np.append(first[1:], len(arr)) - 1
    out = []
    for k, i, j in zip(keys, first, last):
        seg = arr[i:j + 1]
        out.append(Candle(int(k * tf), float(seg[0, 1]), float(seg[:, 2].max()), float(seg[:, 3].min()),
                          float(seg[-1, 4]), float(seg[:, 5].sum())))
    return out


def yearly(days: np.ndarray, eq: np.ndarray) -> dict:
    out = {}
    for y0 in range(2022, 2027):
        a = time.mktime((y0, 1, 1, 0, 0, 0, 0, 0, 0)) - time.timezone
        m = (days >= a) & (days < a + 365.25 * 86400)
        if m.sum() > 5:
            seg = eq[m]
            out[y0] = round((seg[-1] / seg[0] - 1) * 100, 1)
    return out


def run(start: float, end: float, oos: str, bots: list[str], balance: float = 10_000, db: str | None = None) -> dict:
    store = HistoryStore()
    store.load()
    oracle = OracleReplay(store, DATA_DIR / "research" / oos) if "oracle" in bots else None
    hub = MarketHub(SYMBOLS)
    hub.use_wall_clock = False
    hub.clock = start
    replay: dict[int, list[tuple[str, np.ndarray]]] = {}
    for s in SYMBOLS:
        arr = store.raw[s]
        before = arr[arr[:, 0] < start]
        for tf in (3600, 21600, 86400):
            hub.series[s][tf].seed(aggregate(before[-300 * tf // TF:], tf)[-300:], start)
        if len(before):
            hub.on_trade(s, float(before[-1, 4]), 0.0, start - 1)
        for row in arr[(arr[:, 0] >= start) & (arr[:, 0] < end)]:
            replay.setdefault(int(row[0]), []).append((s, row))
    st = None
    if db:  # write the replay into a dashboard database (demo / screenshots)
        from pathlib import Path

        from .storage import Store
        st = Store(Path(db))
        st.clear()
    arena = Arena(hub, st, balance=balance, latency=(0.0, 0.0), oracle=oracle, bot_ids=bots)
    arena.start(start)
    kinds: Counter = Counter()
    daily: dict[str, list[tuple[float, float]]] = {b.id: [] for b in arena.bots}
    t0 = time.time()
    offs = (1, 1200, 2400, 3599)
    for t in sorted(replay):
        rows = replay[t]
        for k, off in enumerate(offs):
            for s, r in rows:
                o, h, lo, c, v = r[1], r[2], r[3], r[4], r[5]
                up = c >= o
                px = (o, lo if up else h, h if up else lo, c)[k]
                hub.on_trade(s, float(px), float(v) / 4, t + off)
            now = t + off
            hub.clock = now
            arena.step(now)
            evs, _ = arena.drain()
            kinds.update(e["kind"] for e in evs)
        if t % 86400 == 82800:  # last hour of the UTC day
            for b in arena.bots:
                daily[b.id].append((t + 3600, b.equity()))
    elapsed = time.time() - t0
    if st:
        arena.save()
    res = {"start": start, "end": end, "elapsed_s": round(elapsed, 1), "fee_tier": arena.fees.tier(hub.now()).name,
           "events": dict(kinds), "bots": {}}
    for b in arena.bots:
        d = np.array(daily[b.id])
        eq = np.concatenate([[balance], d[:, 1]]) if len(d) else np.array([balance, b.equity()])
        days = np.concatenate([[start], d[:, 0]]) if len(d) else np.array([start, end])
        peak = np.maximum.accumulate(eq)
        dr = np.diff(np.log(eq))
        yrs = max((days[-1] - days[0]) / (365.25 * 86400), 1e-9)
        trades = list(b.closed)
        sm = b.summary_json(hub.now())
        res["bots"][b.p.name] = {
            "return_pct": round((eq[-1] / balance - 1) * 100, 1),
            "cagr_pct": round(((eq[-1] / balance) ** (1 / yrs) - 1) * 100, 1),
            "max_dd_pct": round(float((1 - eq / peak).max()) * 100, 1),
            "sharpe": round(float(dr.mean() / (dr.std() + 1e-12) * np.sqrt(365)), 2),
            "trades": sm["all"]["trades"], "win_rate": sm["all"]["win_rate"],
            "avg_r": round(float(np.mean([x["r"] for x in trades])), 2) if trades else None,
            "fees": round(sm["all"]["fees"], 0), "est_tax_ytd": round(sm["tax"]["total"], 0),
            "exits": dict(Counter(x["reason"] for x in trades)), "yearly_pct": yearly(days, eq),
            "open_now": sm["open"],
        }
    return res


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", choices=["dev", "holdout"], default="dev")
    ap.add_argument("--oos", default="oracle_oos_Dr.npz")
    ap.add_argument("--bots", default=",".join(BOTS))
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--end", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default=None)
    ap.add_argument("--override", default="", help='JSON, e.g. {"oracle": {"entry_order": "market"}}')
    a = ap.parse_args()
    if a.override:
        from dataclasses import replace

        from . import config
        ov = json.loads(a.override)
        for i, p in enumerate(config.PROFILES):
            if p.id in ov:
                config.PROFILES[i] = replace(p, **ov[p.id])
    start, end = (OOS_START, HOLDOUT_START) if a.period == "dev" else (HOLDOUT_START, time.time() // TF * TF)
    start = a.start or start
    end = a.end or end
    res = run(start, end, a.oos, a.bots.split(","), db=a.db)
    if a.json:
        print(json.dumps(res, indent=2))
        return
    f = lambda ts: time.strftime("%Y-%m-%d", time.gmtime(ts))  # noqa: E731
    print(f"\n{f(start)} -> {f(end)} · real engine replay · fees {res['fee_tier']} · {res['elapsed_s']}s\n")
    print(f"{'BOT':<10}{'RETURN':>9}{'CAGR':>8}{'MAX DD':>8}{'SHARPE':>8}{'TRADES':>8}{'WIN%':>6}{'AVG R':>7}{'FEES':>9}  YEARLY %")
    for name, v in res["bots"].items():
        wr = f"{v['win_rate'] * 100:.0f}" if v["win_rate"] is not None else "-"
        ar = f"{v['avg_r']:+.2f}" if v["avg_r"] is not None else "-"
        print(f"{name:<10}{v['return_pct']:>8.1f}%{v['cagr_pct']:>7.1f}%{v['max_dd_pct']:>7.1f}%{v['sharpe']:>8.2f}"
              f"{v['trades']:>8}{wr:>6}{ar:>7}{v['fees']:>9,.0f}  {v['yearly_pct']}")
    for name, v in res["bots"].items():
        if v["exits"]:
            print(f"  {name} exits: {v['exits']}")


if __name__ == "__main__":
    main()
