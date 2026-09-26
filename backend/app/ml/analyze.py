"""Robustness check of saved walk-forward predictions using a CAUSAL threshold.

The trade threshold at time t is the q-quantile of predictions ORACLE made during the
previous 30 days - something the live bot can actually know. Results are split by
year to see whether any edge is stable or just one lucky period.

    python -m app.ml.analyze --tag _Er --tp 6 --sl 3 --horizon 720
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from ..config import DATA_DIR
from .data import HistoryStore
from .dataset import build_panel
from .research import FEE_MAKER, FEE_TAKER, HOLDOUT_START, OOS_START, STOP_SLIP

LOOKBACK_H = 30 * 24


def causal_threshold(P: np.ndarray, q: float, lookback: int = LOOKBACK_H, step: int = 24) -> np.ndarray:
    T = P.shape[0]
    thr = np.full(T, np.nan)
    for i in range(lookback, T, step):
        w = P[i - lookback:i].ravel()
        w = w[np.isfinite(w)]
        if len(w) > 500:
            thr[i:i + step] = np.quantile(w, q)
    return thr


def sim(panel, P, thr, lo, hi, max_open: int = 99, one_per_hour: bool = False):
    """Portfolio-ish sim: at most `max_open` concurrent trades, best predictions first."""
    T, S = P.shape
    dt = panel.decision_time
    busy = np.full(S, -1)
    trades = []
    open_until: list[int] = []
    for i in np.where((dt >= lo) & (dt < hi) & np.isfinite(thr))[0]:
        open_until = [u for u in open_until if u > i]
        cand = [(P[i, j], j) for j in range(S)
                if np.isfinite(P[i, j]) and P[i, j] >= thr[i] and busy[j] < i and np.isfinite(panel.ret[i, j])]
        cand.sort(reverse=True)
        if one_per_hour:
            cand = cand[:1]
        for p, j in cand:
            if len(open_until) >= max_open:
                break
            r = float(panel.ret[i, j])
            dv = float(panel.dvol[i, j])
            h = int(panel.held[i, j])
            won = r >= panel.tp_mult * dv * 0.999
            stopped = r <= -panel.sl_mult * dv * 0.999
            net = r - FEE_MAKER - (FEE_MAKER if won else FEE_TAKER + (STOP_SLIP if stopped else 0))
            R = panel.sl_mult * dv
            trades.append((dt[i], j, net, net / R, r / R))
            busy[j] = i + h
            open_until.append(i + h)
    return np.array(trades) if trades else np.zeros((0, 5))


def summarize(tr, label):
    if not len(tr):
        print(f"  {label:<28} no trades")
        return
    years = {}
    for y0 in range(2022, 2027):
        a = time.mktime((y0, 1, 1, 0, 0, 0, 0, 0, 0)) - time.timezone
        m = (tr[:, 0] >= a) & (tr[:, 0] < a + 365.25 * 86400)
        if m.sum():
            years[y0] = f"{tr[m, 3].mean():+.2f}R/{int(m.sum())}"
    se = tr[:, 3].std() / np.sqrt(len(tr))
    print(f"  {label:<28} n={len(tr):>4} win={np.mean(tr[:, 2] > 0):.2f} gross={tr[:, 4].mean():+.3f}R "
          f"net={tr[:, 3].mean():+.3f}R (±{se:.3f}) sum={tr[:, 3].sum():+.1f}R | {years}")


def btc_regime(store: HistoryStore, decision_times: np.ndarray, n: int = 200) -> np.ndarray:
    """True when the last COMPLETED daily BTC close is above its n-day SMA (causal)."""
    from .trend_research import daily_bars, sma
    b = daily_bars(store)["BTC-USD"]
    s = sma(b["c"], n)
    on = b["c"] > s
    day_end = (b["day"] + 1) * 86400
    k = np.searchsorted(day_end, decision_times, side="right") - 1
    out = np.zeros(len(decision_times), dtype=bool)
    ok = k >= 0
    out[ok] = on[k[ok]] & np.isfinite(s[k[ok]])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--tp", type=float, required=True)
    ap.add_argument("--sl", type=float, required=True)
    ap.add_argument("--horizon", type=int, required=True)
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--regime", action="store_true", help="only trade when BTC is above its 200-day SMA")
    a = ap.parse_args()
    st = HistoryStore()
    st.load()
    panel = build_panel(st, tp_mult=a.tp, sl_mult=a.sl, horizon=a.horizon)
    d = np.load(DATA_DIR / "research" / f"oracle_oos{a.tag}.npz")
    P = d["P"].astype(float)
    assert len(P) == len(panel.t)
    if a.regime:
        P[~btc_regime(st, panel.decision_time)] = np.nan
    lo, hi = (HOLDOUT_START, float(panel.decision_time[-1] + 1)) if a.holdout else (OOS_START, HOLDOUT_START)
    print(f"{a.tag} tp={a.tp} sl={a.sl} h={a.horizon}  period={'HOLDOUT' if a.holdout else 'dev'}"
          f"{'  +BTC regime gate' if a.regime else ''}")
    all_thr = np.full(len(P), -1e9)
    summarize(sim(panel, P, all_thr, lo, hi), "every signal (no model)")
    summarize(sim(panel, P, all_thr, lo, hi, max_open=5), "no model, max 5 open")
    for q in (0.8, 0.9, 0.95):
        thr = causal_threshold(P, q)
        summarize(sim(panel, P, thr, lo, hi), f"top {100 - q * 100:.0f}% causal")
        summarize(sim(panel, P, thr, lo, hi, max_open=5), f"top {100 - q * 100:.0f}% causal, max 5 open")


if __name__ == "__main__":
    main()
