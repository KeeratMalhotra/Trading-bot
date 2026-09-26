"""Daily trend-following portfolio research (the NOMAD bot), vs buy-and-hold BTC.

Classic, pre-registered rules (not tuned):
  regime : BTC daily close above its 200-day SMA ("risk-on")
  entry  : coin closes above its prior 20-day high AND above its 50-day SMA, regime on
  exit   : close below the prior 10-day low, OR chandelier stop (highest close - 3 x ATR20),
           OR (variant) regime turns off
  size   : risk 1% of equity per trade with a 2 x ATR20 initial stop, max 25% notional
           per coin, max 100% invested (spot, no leverage)
  costs  : 0.15% maker entry, 0.25% taker + 0.05% slippage exit

    python -m app.ml.trend_research [--holdout]
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from .data import HistoryStore
from .research import HOLDOUT_START, OOS_START

DAY = 86400


def daily_bars(store: HistoryStore):
    out = {}
    for s in store.symbols:
        arr = store.raw[s]
        if not len(arr):
            continue
        d = (arr[:, 0] // DAY).astype(np.int64)
        days = np.unique(d)
        first = np.searchsorted(d, days, side="left")
        last = np.searchsorted(d, days, side="right") - 1
        o = arr[first, 1]
        c = arr[last, 4]
        h = np.maximum.reduceat(arr[:, 2], first)
        lo = np.minimum.reduceat(arr[:, 3], first)
        complete = (last - first + 1) >= 20
        out[s] = {"day": days[complete], "o": o[complete], "h": h[complete], "l": lo[complete], "c": c[complete]}
    return out


def sma(x, n):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def atr(h, l, c, n=20):
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return sma(tr, n)


def run(store, lo, hi, exit_on_regime=False, risk=0.01, max_pos=0.25, entry_n=20, exit_n=10,
        regime_n=200, chand=3.0, stop_atr=2.0):
    bars = daily_bars(store)
    all_days = np.arange(lo // DAY, hi // DAY, dtype=np.int64)
    ind = {}
    for s, b in bars.items():
        c = b["c"]
        idx = {int(d): i for i, d in enumerate(b["day"])}
        hi_n = np.full(len(c), np.nan)
        lo_n = np.full(len(c), np.nan)
        for i in range(entry_n, len(c)):
            hi_n[i] = b["h"][i - entry_n:i].max()
        for i in range(exit_n, len(c)):
            lo_n[i] = b["l"][i - exit_n:i].min()
        ind[s] = {"idx": idx, "c": c, "sma50": sma(c, 50), "atr": atr(b["h"], b["l"], c), "hi": hi_n, "lo": lo_n,
                  "reg": sma(c, regime_n)}
    btc = ind["BTC-USD"]
    cash, eq_curve, trades = 1.0, [], []
    pos: dict[str, dict] = {}
    fee_in, fee_out = 0.0015, 0.0025 + 0.0005
    for d in all_days:
        prices = {s: v["c"][v["idx"][d]] for s, v in ind.items() if d in v["idx"]}
        bi = btc["idx"].get(int(d))
        regime = bi is not None and np.isfinite(btc["reg"][bi]) and btc["c"][bi] > btc["reg"][bi]
        # exits (decided on today's close, executed at today's close)
        for s in list(pos):
            v, p = ind[s], pos[s]
            i = v["idx"].get(int(d))
            if i is None:
                continue
            c = v["c"][i]
            p["peak"] = max(p["peak"], c)
            stop = max(p["stop"], p["peak"] - chand * v["atr"][i])
            p["stop"] = stop
            if c < stop or (np.isfinite(v["lo"][i]) and c < v["lo"][i]) or (exit_on_regime and not regime):
                proceeds = p["qty"] * c * (1 - fee_out)
                cash += proceeds
                trades.append((d, s, proceeds / p["cost"] - 1, (c - p["entry"]) / p["r"], d - p["day"]))
                del pos[s]
        equity = cash + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in pos.items())
        # entries
        if regime:
            cands = []
            for s, v in ind.items():
                i = v["idx"].get(int(d))
                if i is None or s in pos or not np.isfinite(v["hi"][i]) or not np.isfinite(v["sma50"][i]):
                    continue
                c = v["c"][i]
                if c > v["hi"][i] and c > v["sma50"][i] and np.isfinite(v["atr"][i]):
                    mom = c / v["c"][max(0, i - 60)] - 1
                    cands.append((mom, s, c, v["atr"][i]))
            for mom, s, c, a in sorted(cands, reverse=True):
                r = stop_atr * a
                qty = equity * risk / r
                notional = min(qty * c, equity * max_pos, cash / (1 + fee_in))
                if notional < equity * 0.02:
                    continue
                qty = notional / c
                cash -= notional * (1 + fee_in)
                pos[s] = {"qty": qty, "entry": c, "cost": notional * (1 + fee_in), "stop": c - r, "r": r,
                          "peak": c, "day": d}
        equity = cash + sum(p["qty"] * prices.get(s, p["entry"]) for s, p in pos.items())
        eq_curve.append((d, equity, len(pos)))
    eq = np.array(eq_curve)
    b = bars["BTC-USD"]
    bidx = {int(d): i for i, d in enumerate(b["day"])}
    hodl = np.array([b["c"][bidx[int(d)]] if int(d) in bidx else np.nan for d in eq[:, 0]])
    for i in range(1, len(hodl)):  # forward-fill missing days
        if not np.isfinite(hodl[i]):
            hodl[i] = hodl[i - 1]
    hodl = hodl / hodl[0] * (1 - 0.0025)
    return eq, np.array(trades, dtype=object), hodl


def stats(eq, label):
    e = eq
    ret = e[-1] / e[0] - 1
    peak = np.maximum.accumulate(e)
    dd = (1 - e / peak).max()
    days = len(e)
    cagr = (e[-1] / e[0]) ** (365 / days) - 1
    dr = np.diff(np.log(e))
    sharpe = dr.mean() / (dr.std() + 1e-12) * np.sqrt(365)
    return f"{label:<26} total {ret * 100:+7.1f}%  CAGR {cagr * 100:+6.1f}%  maxDD {dd * 100:5.1f}%  Sharpe {sharpe:5.2f}"


def yearly(eq_days, e):
    out = {}
    for y0 in range(2022, 2027):
        a = (time.mktime((y0, 1, 1, 0, 0, 0, 0, 0, 0)) - time.timezone) // DAY
        m = (eq_days >= a) & (eq_days < a + 365)
        if m.sum() > 5:
            seg = e[m]
            out[y0] = f"{(seg[-1] / seg[0] - 1) * 100:+.0f}%"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    st = HistoryStore()
    st.load()
    lo, hi = (HOLDOUT_START, time.time()) if a.holdout else (OOS_START, HOLDOUT_START)
    print("period:", "HOLDOUT" if a.holdout else "development", time.strftime("%Y-%m-%d", time.gmtime(lo)), "->",
          time.strftime("%Y-%m-%d", time.gmtime(hi)))
    for variant, kw in (("NOMAD (trail/10d exit)", {}), ("NOMAD + regime exit", {"exit_on_regime": True})):
        eq, tr, hodl = run(st, lo, hi, **kw)
        e = eq[:, 1].astype(float)
        print(stats(e, variant), f"| trades {len(tr)}, avg invested {eq[:, 2].mean():.1f} coins |", yearly(eq[:, 0], e))
        if len(tr):
            r = np.array([t[3] for t in tr], dtype=float)
            print(f"{'':<26} win {np.mean(np.array([t[2] for t in tr], dtype=float) > 0):.2f}  avg {r.mean():+.2f}R  "
                  f"best {r.max():+.1f}R  worst {r.min():+.1f}R  avg hold {np.mean([t[4] for t in tr]):.0f}d")
    print(stats(hodl, "HODL BTC"), "|", yearly(eq[:, 0], hodl))


if __name__ == "__main__":
    main()
