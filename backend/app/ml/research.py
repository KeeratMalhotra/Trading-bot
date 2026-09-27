"""Walk-forward research for ORACLE.

    python -m app.ml.research                 # development period only
    python -m app.ml.research --holdout       # ALSO reveal the untouched holdout (do this once, at the end)

Protocol
  * expanding-window retrain every RETRAIN_DAYS; each model only sees labels that were
    fully known EMBARGO before its cutoff (purged walk-forward, no look-ahead)
  * development period: 2022-01-01 .. 2026-03-26
  * holdout: 2026-03-26 .. today - never used while designing the model
  * out-of-sample predictions are saved so the full trading engine can replay them
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time

import numpy as np
from sklearn.metrics import roc_auc_score

from ..config import DATA_DIR
from .data import HistoryStore
from .dataset import build_panel
from .model import fit

OOS_START = 1640995200       # 2022-01-01
HOLDOUT_START = 1774483200   # 2026-03-26
RETRAIN_DAYS = 30
FEE_MAKER, FEE_TAKER, STOP_SLIP = 0.0015, 0.0025, 0.0005


def walk_forward(panel, start: float, end: float, retrain_days: int = RETRAIN_DAYS, log=print, kind: str = "clf"):
    T, S = panel.y.shape
    P = np.full((T, S), np.nan, dtype=np.float32)
    dt = panel.decision_time
    cut = start
    models = []
    while cut < end:
        nxt = min(cut + retrain_days * 86400, end)
        t0 = time.time()
        m = fit(panel, cut, kind=kind)
        rows = np.where((dt >= cut) & (dt < nxt))[0]
        Xr = panel.X[rows].reshape(-1, panel.X.shape[2])
        ok = np.isfinite(Xr).all(axis=1)
        pr = np.full(len(Xr), np.nan, dtype=np.float32)
        if ok.any():
            pr[ok] = m.predict(Xr[ok])
        P[rows] = pr.reshape(len(rows), S)
        models.append({"cutoff": cut, "n": m.n_samples, "base": m.base_rate})
        log(f"  model @ {time.strftime('%Y-%m-%d', time.gmtime(cut))}: {m.n_samples:,} samples, "
            f"base rate {m.base_rate:.3f}, {time.time() - t0:.1f}s")
        cut = nxt
    return P, models


def trade_sim(panel, P, mask_t, thr: float, top1: bool = False):
    """Non-overlapping per-coin trades whenever P >= thr. Returns per-trade net returns and R.
    top1: only the single highest-probability coin per hour may be entered."""
    T, S = P.shape
    out_ret, out_r, out_t, out_g = [], [], [], []
    if top1:
        Pm = np.where(np.isfinite(P), P, -1)
        best = Pm.argmax(axis=1)
        keep = np.zeros_like(P, dtype=bool)
        keep[np.arange(T), best] = True
        P = np.where(keep, P, np.nan)
    for j in range(S):
        i = 0
        idx = np.where(mask_t & np.isfinite(P[:, j]) & np.isfinite(panel.ret[:, j]))[0]
        busy_until = -1
        for i in idx:
            if i <= busy_until or P[i, j] < thr:
                continue
            r = float(panel.ret[i, j])
            held = int(panel.held[i, j])
            dv = float(panel.dvol[i, j])
            won = r >= panel.tp_mult * dv * 0.999
            stopped = r <= -panel.sl_mult * dv * 0.999
            exit_fee = FEE_MAKER if won else FEE_TAKER + (STOP_SLIP if stopped else 0)
            net = r - FEE_MAKER - exit_fee
            out_ret.append(net)
            out_r.append(net / (panel.sl_mult * dv))
            out_g.append(r / (panel.sl_mult * dv))
            out_t.append(panel.t[i])
            busy_until = i + held
    return np.array(out_ret), np.array(out_r), np.array(out_t), np.array(out_g)


def report(panel, P, lo: float, hi: float, label: str) -> dict:
    dt = panel.decision_time
    mt = (dt >= lo) & (dt < hi)
    yy = panel.y[mt].ravel()  # AUC is always measured against "take-profit hit first"
    pp = P[mt].ravel()
    ok = np.isfinite(yy) & np.isfinite(pp)
    yy, pp = yy[ok], pp[ok]
    res = {"period": label, "samples": int(len(yy)), "base_rate": float(yy.mean()),
           "auc": float(roc_auc_score(yy, pp)), "brier": float(np.mean((pp - yy) ** 2)),
           "brier_base": float(np.mean((yy.mean() - yy) ** 2))}
    qs = np.quantile(pp, np.linspace(0, 1, 11))
    cal = []
    for a, b in zip(qs[:-1], qs[1:]):
        m = (pp >= a) & (pp <= b)
        cal.append([round(float(pp[m].mean()), 3), round(float(yy[m].mean()), 3), int(m.sum())])
    res["calibration_deciles"] = cal
    years = {}
    tt = np.repeat(dt[mt], P.shape[1])[ok]
    for y0 in range(2022, 2027):
        a = time.mktime((y0, 1, 1, 0, 0, 0, 0, 0, 0)) - time.timezone
        m = (tt >= a) & (tt < a + 365.25 * 86400)
        if m.sum() > 1000 and len(np.unique(yy[m])) == 2:
            years[y0] = round(float(roc_auc_score(yy[m], pp[m])), 4)
    res["auc_by_year"] = years
    sims = {}
    qv = np.quantile(pp, [0.0, 0.8, 0.9, 0.95, 0.98, 0.99])
    for name, thr in zip(["all", "top20%", "top10%", "top5%", "top2%", "top1%"], qv):
        for top1 in (False, True):
            net, r, _, g = trade_sim(panel, P, mt, float(thr), top1)
            if len(r):
                sims[name + (" best-coin" if top1 else "")] = {
                    "thr": round(float(thr), 3), "trades": int(len(r)), "win_rate": round(float((net > 0).mean()), 3),
                    "gross_R": round(float(g.mean()), 3), "net_R": round(float(r.mean()), 3),
                    "avg_net_pct": round(float(net.mean() * 100), 3), "sum_R": round(float(r.sum()), 1)}
    res["trade_sim"] = sims
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true", help="also evaluate the untouched holdout")
    ap.add_argument("--retrain-days", type=int, default=RETRAIN_DAYS)
    ap.add_argument("--no-update", action="store_true")
    ap.add_argument("--tp", type=float, default=2.0)
    ap.add_argument("--sl", type=float, default=1.0)
    ap.add_argument("--horizon", type=int, default=72)
    ap.add_argument("--tag", default="")
    ap.add_argument("--kind", default="clf", choices=["clf", "reg"])
    ap.add_argument("--side", default="long", choices=["long", "short"])
    a = ap.parse_args()
    store = HistoryStore()
    store.load()
    if not a.no_update:
        asyncio.run(store.update())
    t0 = time.time()
    panel = build_panel(store, tp_mult=a.tp, sl_mult=a.sl, horizon=a.horizon, side=a.side)
    print(f"panel: {panel.X.shape} features={len(panel.names)} built in {time.time() - t0:.1f}s")
    end = float(panel.decision_time[-1] + 1)
    P, models = walk_forward(panel, OOS_START, end, a.retrain_days, kind=a.kind)
    out = DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / f"oracle_oos{a.tag}.npz", t=panel.t, P=P, symbols=np.array(panel.symbols))
    results = {"dev": report(panel, P, OOS_START, HOLDOUT_START, "2022-01-01..2026-03-26 (development)")}
    if a.holdout:
        results["holdout"] = report(panel, P, HOLDOUT_START, end, "2026-03-26..now (HOLDOUT)")
    results["label"] = {"tp": a.tp, "sl": a.sl, "horizon_h": a.horizon, "kind": a.kind}
    (out / f"oracle_report{a.tag}.json").write_text(json.dumps(results, indent=2))
    for k in ("dev", "holdout"):
        if k not in results:
            continue
        r = results[k]
        print(f"\n== {r['period']} ==  AUC {r['auc']:.4f}  base rate {r['base_rate']:.3f}  by year {r['auc_by_year']}")
        print("   calibration (pred, actual): " + " ".join(f"{a_:.2f}/{b_:.2f}" for a_, b_, _ in r["calibration_deciles"]))
        for name, v in r["trade_sim"].items():
            print(f"   {name:<22} thr {v['thr']:.3f} trades {v['trades']:>5}  win {v['win_rate']:.2f}  "
                  f"gross {v['gross_R']:+.3f}R  net {v['net_R']:+.3f}R  sum {v['sum_R']:+.1f}R")


if __name__ == "__main__":
    main()
