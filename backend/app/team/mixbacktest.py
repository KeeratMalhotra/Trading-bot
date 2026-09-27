"""'QUORUM + BTC' what-if: the rule the dashboard uses, and a long backtest for its Backtest tab.

    python -m app.team.mixbacktest build --start 2017-01-01    # needs the long history (README: Longer history)
    python -m app.team.mixbacktest grid                        # BTC share x rebalancing period, from the saved file

`build` replays the real engine (same code as app.team.replay) and stores its daily value next to BTC's
daily close in backtest_mix.json. That file is committed, so the dashboard can show the backtest without
the long history. The dashboard mixes the two series with MIX_BTC_SHARE / MIX_REBALANCE using mix_path(),
the same rule the live what-if box uses.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

PATH = Path(__file__).with_name("backtest_mix.json")
MIX_FEE = 0.006      # spot fee on the BTC the what-if buys or sells (small-account maker)
DAY = 86400
NAMES = {0: "never rebalanced", 1: "rebalanced monthly", 3: "rebalanced quarterly", 6: "rebalanced every 6 months",
         12: "rebalanced yearly", 24: "rebalanced every 2 years"}
ALIASES = {"never": 0, "none": 0, "hold": 0, "monthly": 1, "quarterly": 3, "semiannual": 6, "semi-annual": 6,
           "halfyearly": 6, "half-yearly": 6, "yearly": 12, "annual": 12, "annually": 12, "biennial": 24}


def rebalance_months(spec: str | int) -> int:
    """'never' -> 0, 'yearly' -> 12, '6', '6m' or 'semiannual' -> 6, '2y' -> 24 (months; 0 = never)."""
    s = str(spec).strip().lower()
    if s in ALIASES:
        return ALIASES[s]
    try:
        if s.endswith("y"):
            return max(0, int(float(s[:-1]) * 12))
        return max(0, int(float(s.rstrip("m"))))
    except ValueError:
        return 12


def rebalance_label(months: int) -> str:
    return NAMES.get(months) or (f"rebalanced every {months} months" if months % 12
                                 else f"rebalanced every {months // 12} years")


def period(year: int, month: int, months: int, phase: int = 0) -> int:
    """Calendar period number; the mix is rebalanced when it changes (months=6: on Jan 1 and Jul 1)."""
    return (year * 12 + month - 1 - phase) // months if months else 0


def mix_path(days, q, btc, share: float, months: int, fee: float = MIX_FEE, phase: int = 0) -> np.ndarray:
    """Value of $1 put (1-share) into QUORUM and `share` into BTC at the close of days[0] (one spot fee),
    rebalanced back to the split at the close of the last day of each `months`-long calendar period
    (0 = never), paying the fee on the BTC bought or sold. q: QUORUM value, btc: BTC price, per day."""
    days = np.asarray(days)
    q = np.asarray(q, float)
    btc = np.asarray(btc, float)
    per = np.array([period(t.tm_year, t.tm_mon, months, phase) for t in (time.gmtime(int(x) * DAY) for x in days)])
    vq, vb = 1.0 - share, share * (1 - fee)
    out = np.empty(len(days))
    out[0] = vq + vb
    for i in range(1, len(days)):
        vq *= q[i] / q[i - 1]
        vb *= btc[i] / btc[i - 1]
        if months and i + 1 < len(days) and per[i + 1] != per[i]:
            tot = vq + vb
            tot -= abs(share * tot - vb) * fee
            vq, vb = (1 - share) * tot, share * tot
        out[i] = vq + vb
    return out


def stats(days, v) -> dict:
    days = np.asarray(days)
    v = np.asarray(v, float)
    yrs = max((days[-1] - days[0]) / 365.25, 1e-9)
    r = v[1:] / v[:-1] - 1
    yy = np.array([time.gmtime(int(x) * DAY).tm_year for x in days])
    years = {}
    for y in np.unique(yy):
        idx = np.where(yy == y)[0]
        if len(idx) < 20:
            continue
        base = v[idx[0] - 1] if idx[0] > 0 else v[0]
        years[int(y)] = float(v[idx[-1]] / base - 1)
    return {"final": float(v[-1]), "total": float(v[-1] / v[0] - 1), "cagr": float((v[-1] / v[0]) ** (1 / yrs) - 1),
            "max_dd": float((1 - v / np.maximum.accumulate(v)).max()),
            "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0, "years": years}


def crashes(v, n: int = 2) -> list[tuple[int, int]]:
    """The n deepest peak-to-trough falls of `v` that don't overlap (a fall lasts until the old peak is regained)."""
    v = np.asarray(v, float)
    free = np.ones(len(v), bool)
    out = []
    for _ in range(n):
        best, bi, bj = 0.0, -1, -1
        peak_i = None
        for k in range(len(v)):
            if not free[k]:
                peak_i = None
                continue
            if peak_i is None or v[k] > v[peak_i]:
                peak_i = k
            dd = 1 - v[k] / v[peak_i]
            if dd > best:
                best, bi, bj = dd, peak_i, k
        if bi < 0 or best < 0.2:
            break
        out.append((bi, bj))
        rec = next((k for k in range(bj, len(v)) if v[k] >= v[bi]), len(v))
        free[bi:rec] = False
    return sorted(out)


# ------------------------------------------------------------------ saved backtest
def load(path: Path = PATH) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=16)
def dashboard(share: float, months: int, points: int = 1400) -> dict:
    """What the dashboard's Backtest tab shows: BTC held alone vs the what-if mix, in dollars from the
    backtest's starting balance, plus stats and the two deepest BTC crashes."""
    bt = load()
    if not bt:
        return {"available": False}
    days = np.array(bt["days"])
    q, btc = np.array(bt["quorum"]), np.array(bt["btc"])
    bal = float(bt["balance"])
    paths = {"btc": bal * mix_path(days, q, btc, 1.0, 0), "mix": bal * mix_path(days, q, btc, share, months),
             "quorum": bal * q}
    step = max(1, len(days) // points)
    keep = np.unique(np.r_[np.arange(0, len(days), step), len(days) - 1])
    ts = (days + 1) * DAY                        # each value is at that day's close
    win = crashes(paths["btc"])
    return {
        "available": True, "generated": bt["generated"], "note": bt["note"], "balance": bal,
        "start": int(ts[0]), "end": int(ts[-1]), "btc_share": share, "rebalance_months": months,
        "rebalance": rebalance_label(months),
        "series": {k: [[int(ts[i]), round(float(p[i]), 2)] for i in keep] for k, p in paths.items()},
        "stats": {k: stats(days, p) for k, p in paths.items()},
        "crashes": [{"from": int(ts[i]), "to": int(ts[j]),
                     **{k: float(p[j] / p[i] - 1) for k, p in paths.items()}} for i, j in win],
    }


def build(start: float, end: float, balance: float = 100_000.0) -> dict:
    from ..ml.altdata import AltData
    from ..ml.data import HistoryStore
    from .replay import run
    from .research import load_daily
    out = run(start, end, balance, verbose=False)
    store = HistoryStore()
    store.load()
    alt = AltData()
    alt.load()
    d = load_daily(store, alt)
    b = d.symbols.index("BTC-USD")
    pos = {int(x): i for i, x in enumerate(d.days)}
    rows = [(int(start // DAY) - 1, balance)] + [(int(t // DAY) - 1, e) for t, e in out["daily"]]
    rows = [(k, e) for k, e in rows if k in pos and np.isfinite(d.C[pos[k], b])]
    fmt = lambda k: time.strftime("%Y-%m-%d", time.gmtime((k + 1) * DAY))  # noqa: E731
    res = {"generated": time.strftime("%Y-%m-%d", time.gmtime()), "balance": balance,
           "from": fmt(rows[0][0]), "to": fmt(rows[-1][0]),
           "note": "Hypothetical backtest: the live engine replayed hour by hour on Coinbase data, ORACLE retrained "
                   "walk-forward from Sept 2016. Assumes today's US perpetual-style futures and fees existed; "
                   "funding from BitMEX/Deribit; 2022 onward is the period the team was designed on. "
                   "BTC trades pay a 0.6% spot fee. Before tax. Not live results.",
           "days": [k for k, _ in rows], "quorum": [round(e / balance, 6) for _, e in rows],
           "btc": [round(float(d.C[pos[k], b]), 2) for k, _ in rows]}
    PATH.write_text(json.dumps(res, separators=(",", ":")))
    dashboard.cache_clear()
    return res


def grid(shares=(0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0), periods=(1, 3, 6, 12, 24, 0)) -> None:
    bt = load()
    if not bt:
        raise SystemExit("no saved backtest: run `python -m app.team.mixbacktest build` first")
    days, q, btc = np.array(bt["days"]), np.array(bt["quorum"]), np.array(bt["btc"])
    bal = bt["balance"]
    print(f"${bal:,.0f} from {bt['from']} to {bt['to']} · rows: BTC share · columns: rebalancing (months, 0 = never)\n")
    res = {(s, m): stats(days, bal * mix_path(days, q, btc, s, m)) for s in shares for m in periods}
    for key, label, f in (("final", "final value ($M)", lambda x: f"{x / 1e6:8.2f}"),
                          ("max_dd", "worst drop", lambda x: f"{x * 100:7.0f}%"),
                          ("sharpe", "Sharpe", lambda x: f"{x:8.2f}")):
        print(f"{label:<18}" + "".join(f"{m:>8}" for m in periods))
        for s in shares:
            print(f"  {s * 100:>3.0f}% BTC        " + "".join(f(res[(s, m)][key]) for m in periods))
        print()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--start", default="2017-01-01")
    b.add_argument("--end", default=None)
    b.add_argument("--balance", type=float, default=100_000)
    sub.add_parser("grid")
    a = ap.parse_args()
    if a.cmd == "build":
        ts = lambda s: datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()  # noqa: E731
        end = ts(a.end) if a.end else time.time() // DAY * DAY
        res = build(ts(a.start), end, a.balance)
        print(f"saved {PATH.name}: {res['from']} .. {res['to']}, {len(res['days'])} days, "
              f"QUORUM x{res['quorum'][-1]:.1f}, BTC x{res['btc'][-1] / res['btc'][0]:.1f}")
    else:
        grid()


if __name__ == "__main__":
    main()
