"""Replay the QUORUM engine hour by hour over real history and compare with the research sim.

    python -m app.team.replay --start 2022-01-01 [--end 2026-09-26] [--balance 30000]

This runs the actual engine code (book, contract rounding, fees, funding, netting, risk
limits, ORACLE hourly stops/targets) - the research sim runs on daily closes with continuous
position sizes. If the two agree, the live engine does what the research says.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

import numpy as np

from ..ml.altdata import AltData
from ..ml.data import TF, HistoryStore
from .engine import AGENTS, Quorum
from .forecaster import ReplayForecaster
from .research import CostModel, compute_all, stats

SPREAD = {"BTC-USD": 0.0001, "ETH-USD": 0.0002}


def run(start: float, end: float, balance: float, spot_maker: float = 0.006, spot_taker: float = 0.012,
        verbose: bool = True, db_path: str | None = None, cash_apy: float = 0.0) -> dict:
    store = HistoryStore()
    store.load()
    alt = AltData()
    alt.load()
    costs = CostModel.for_spot_fee(spot_maker, spot_taker)
    t0 = time.time()
    desk = compute_all(store, alt, costs=costs)
    fc = ReplayForecaster(store)
    have = np.where(np.isfinite(fc.P["long"]).any(axis=1))[0]
    first_fc = float(fc.t[have[0]]) if len(have) else float("inf")
    if verbose and start < first_fc:
        print(f"note: ORACLE's out-of-sample forecasts only start {time.strftime('%Y-%m-%d', time.gmtime(first_fc))} "
              "(it can trade 30 days after that). For earlier starts see 'Longer history' in the README.")
    syms = store.symbols
    idx = {s: {int(r[0]): r for r in store.raw[s]} for s in syms}
    cur: dict[str, tuple] = {}

    def prices():
        return cur

    def bars(s, bar_t):
        r = idx[s].get(int(bar_t))
        return (r[1], r[2], r[3], r[4]) if r is not None else None

    clock = {"t": start}
    db = None
    if db_path:   # write the replay into a dashboard database (demo / screenshots)
        from pathlib import Path

        from ..storage import Store
        db = Store(Path(db_path))
        db.clear()
    q = Quorum(syms, balance, prices, bars, fc, None, db, store, alt, desk_source=desk, costs=costs,
               clock=lambda: clock["t"], cash_apy=cash_apy)
    q.fees.mode = next((t.name for t in __import__("app.config", fromlist=["x"]).COINBASE_FEE_TIERS
                        if abs(t.maker - spot_maker) < 1e-9), "Intro 1")
    q.started = start
    daily = []
    def quote(s, p):
        sp = SPREAD.get(s, 0.0005) / 2
        cur[s] = (p * (1 - sp), p * (1 + sp), p)

    for bar in range(int(start // TF * TF), int(end // TF * TF), TF):
        rows = {s: idx[s].get(bar) for s in syms}
        # 4 ticks inside the hour (open, low/high, high/low, close) so stops & targets trigger
        # intrabar like they do live, then the decision step right after the close
        for k, off in enumerate((60, 1200, 2400, 3590)):
            for s, r in rows.items():
                if r is None:
                    continue
                o, h, lo, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
                quote(s, (o, lo if c >= o else h, h if c >= o else lo, c)[k])
            clock["t"] = bar + off
            q.step(clock["t"])
        clock["t"] = bar + TF + 5          # decision step right after the close
        q.step(clock["t"])
        if (bar + TF) % 86400 == 0:
            daily.append((bar + TF, q.equity()))
            if db is not None:
                db.add_events(q.drain())
        elif db is None and len(q.outbox) > 5000:
            q.drain()
    if db is not None:
        db.add_events(q.drain())
        db.kv_set("quorum", q.dump())
    dd = np.array(daily)
    eq = dd[:, 1]
    r = np.diff(eq) / eq[:-1]
    days_idx = (dd[1:, 0] // 86400 - 1).astype(np.int64)
    # align with research team returns for the same days
    m = np.isin(desk.d.days, days_idx)
    res_r = desk.team[m]
    fake = type("D", (), {})()
    fake.days = days_idx
    eng_stats = stats(fake, r, lo=start)
    fake2 = type("D", (), {})()
    fake2.days = desk.d.days[m]
    sim_stats = stats(fake2, res_r, lo=start)
    out = {"elapsed_s": round(time.time() - t0, 1), "engine": eng_stats, "research_sim": sim_stats,
           "trades": len(q.fills), "oracle_trades": len(q.oracle_closed), "fees": q.book.fees,
           "funding": round(q.book.funding_total, 2), "interest": round(q.book.interest_total, 2),
           "final_equity": round(q.equity(), 2),
           "mix_final": None if q.mix_value is None else round(q.mix_value, 2),
           "agents_pnl": {a: round(q.agent_pnl[a]["all"], 2) for a in AGENTS}}
    if verbose:
        f = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
        print(f"\n{f(start)} -> {f(end)} · ${balance:,.0f} · {out['elapsed_s']}s · costs {costs}")
        for k in ("engine", "research_sim"):
            v = out[k]
            print(f"  {k:<13} total {v['total_pct']:>7.1f}%  maxDD {v['max_dd_pct']:>5.1f}%  Sharpe {v['sharpe']:>5.2f}  "
                  f"months {v['up_months']}/{v['down_months']}  worst {v['worst_month_pct']:.1f}%  {v['yearly_pct']}")
        print(f"  fills {out['trades']} (last 300 kept) · ORACLE trades {out['oracle_trades']} · fees {out['fees']} · "
              f"funding {out['funding']} · interest {out['interest']} · final equity {out['final_equity']:,.0f} · "
              f"agents {out['agents_pnl']}")
        if out["mix_final"] is not None:
            print(f"  what-if {(1 - q.mix_share) * 100:.0f}% QUORUM + {q.mix_share * 100:.0f}% BTC "
                  f"({q.mix_rebalance} rebalance): final value {out['mix_final']:,.0f}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--balance", type=float, default=30_000)
    ap.add_argument("--spot-maker", type=float, default=0.006)
    ap.add_argument("--spot-taker", type=float, default=0.012)
    ap.add_argument("--db", default=None)
    ap.add_argument("--cash-apy", type=float, default=0.0, help="yield on idle cash, e.g. 0.0375 (default off)")
    a = ap.parse_args()
    ts = lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
    end = ts(a.end) if a.end else time.time() // 86400 * 86400
    run(ts(a.start), end, a.balance, a.spot_maker, a.spot_taker, db_path=a.db, cash_apy=a.cash_apy)


if __name__ == "__main__":
    main()
