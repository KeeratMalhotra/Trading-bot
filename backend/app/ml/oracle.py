"""ORACLE runtime: live forecasting service + a replay twin for backtests.

Both expose the same tiny interface used by the `oracle` strategy:
    .ready, .status_text, .prediction(symbol, bar_t) -> dict | None

Frozen design (chosen on 2022-01..2026-03 walk-forward, then checked on an untouched holdout):
  model     gradient-boosted regression trees -> expected outcome of a trade in R
  trade     stop 2 x daily-vol, target 4 x daily-vol, 14-day time limit
  filter    only when BTC's last daily close is above its 200-day average
  bar       forecast must be in the top 10% of ORACLE's own forecasts from the previous 30 days
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path

import numpy as np

from ..config import DATA_DIR
from .data import TF, HistoryStore
from .dataset import build_panel

log = logging.getLogger("ml.oracle")

TP_MULT, SL_MULT, HORIZON = 4.0, 2.0, 336
THRESHOLD_Q = 0.90
THRESHOLD_LOOKBACK = 30 * 24
RETRAIN_EVERY = 24 * 3600
TRACK_RECORD = Path(__file__).with_name("oracle_track_record.json")


def btc_regime_on(store: HistoryStore, decision_times: np.ndarray, n: int = 200) -> np.ndarray:
    """True when the last COMPLETED daily BTC close is above its n-day SMA (causal)."""
    arr = store.raw.get("BTC-USD")
    out = np.zeros(len(decision_times), dtype=bool)
    if arr is None or len(arr) == 0:
        return out
    d = (arr[:, 0] // 86400).astype(np.int64)
    days = np.unique(d)
    last = np.searchsorted(d, days, side="right") - 1
    closes = arr[last, 4]
    sma = np.full(len(closes), np.nan)
    if len(closes) >= n:
        cs = np.cumsum(np.insert(closes, 0, 0.0))
        sma[n - 1:] = (cs[n:] - cs[:-n]) / n
    on = closes > sma
    day_end = (days + 1) * 86400
    k = np.searchsorted(day_end, decision_times, side="right") - 1
    ok = k >= 0
    out[ok] = on[k[ok]] & np.isfinite(sma[k[ok]])
    return out


def _reasons(panel, row: int, j: int) -> list[str]:
    names = {n: i for i, n in enumerate(panel.names)}
    X = panel.X[row, j]
    c = panel.close[:, j]
    out = []
    if row >= 168 and np.isfinite(c[row - 168]):
        r7 = c[row] / c[row - 168] - 1
        rank = X[names["cs_rank_168"]]
        n = int(np.isfinite(panel.X[row, :, 0]).sum())
        place = int(round((1 - rank) * (n - 1))) + 1 if np.isfinite(rank) else None
        out.append(f"7-day move {r7 * 100:+.1f}%" + (f", #{place} of {n} coins" if place else ""))
    d200 = X[names["dist_ema200"]]
    if np.isfinite(d200):
        out.append("Above its 200-hour trend line" if d200 > 0 else "Below its 200-hour trend line")
    v = X[names["vol_24_168"]]
    if np.isfinite(v):
        out.append("Volatility compressed (coiled)" if v < -0.2 else
                   "Volatility expanding" if v > 0.2 else "Normal volatility")
    return out


class _Base:
    ready = False
    status_text = "ORACLE is warming up."

    def __init__(self):
        self.preds: dict[int, dict[str, dict]] = {}

    def prediction(self, symbol: str, bar_t: int | None = None) -> dict | None:
        if not self.preds:
            return None
        if bar_t is None:
            bar_t = max(self.preds)
        return self.preds.get(int(bar_t), {}).get(symbol)

    _track: dict | None = None

    def track_record(self) -> dict | None:
        if self._track is None:
            try:
                self._track = json.loads(TRACK_RECORD.read_text())
            except Exception:  # noqa: BLE001
                return None
        return self._track


class OracleService(_Base):
    """Live: keeps the hourly history fresh, retrains daily, forecasts every coin every hour."""

    def __init__(self, root: Path | None = None):
        super().__init__()
        self.store = HistoryStore(root or (DATA_DIR / "history"))
        self.model_path = (root or (DATA_DIR / "history")).parent / "oracle_model.pkl"
        self.model = None
        self.state = "starting"
        self.hist: deque[tuple[int, np.ndarray]] = deque(maxlen=THRESHOLD_LOOKBACK + 48)
        self.latest: dict | None = None
        self.error = ""

    @property
    def ready(self) -> bool:  # type: ignore[override]
        return self.model is not None and bool(self.preds)

    @property
    def status_text(self) -> str:  # type: ignore[override]
        if self.state == "downloading":
            return f"Downloading 6 years of hourly history ({self.store.progress * 100:.0f}%)..."
        if self.state == "training":
            return "Training on 6 years of market history..."
        if self.state == "error":
            return f"Model offline: {self.error}"
        if not self.ready:
            return "ORACLE is warming up."
        return "Forecasting"

    # ----------------------------------------------------------- lifecycle
    async def run(self, stop: asyncio.Event) -> None:
        from .model import OracleModel
        self.store.load()
        m = OracleModel.load(self.model_path)
        if m is not None and time.time() - m.trained_at < 3 * 86400:
            self.model = m
        while not stop.is_set():
            try:
                first = not any(len(a) for a in self.store.raw.values())
                self.state = "downloading" if first else "updating"
                await self.store.update()
                if self.model is None or time.time() - self.model.trained_at > RETRAIN_EVERY:
                    self.state = "training"
                    self.model = await asyncio.to_thread(self._train)
                    self.hist.clear()
                await asyncio.to_thread(self._forecast)
                self.state = "ready"
            except Exception as e:  # noqa: BLE001
                log.exception("oracle cycle failed")
                self.state, self.error = "error", str(e)[:120]
            # wake up 90s after the next hourly close (REST candles settle by then)
            now = time.time()
            nxt = (now // TF + 1) * TF + 90
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(30.0, nxt - now))
            except asyncio.TimeoutError:
                pass

    def _train(self):
        from threadpoolctl import threadpool_limits

        from .model import fit
        t0 = time.time()
        with threadpool_limits(4):
            panel = build_panel(self.store, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON)
            m = fit(panel, time.time(), kind="reg")
        m.info = {"train_seconds": round(time.time() - t0, 1), "features": len(m.feature_names),
                  "coins": len(panel.symbols), "hours": int(len(panel.t))}
        m.save(self.model_path)
        log.info("ORACLE trained on %s samples in %.1fs", m.n_samples, time.time() - t0)
        return m

    def _forecast(self) -> None:
        from threadpoolctl import threadpool_limits
        panel = build_panel(self.store, with_labels=False, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON)
        T, S, F = panel.X.shape
        need = THRESHOLD_LOOKBACK + 1 if not self.hist else 1
        rows = np.arange(max(0, T - need), T)
        known = {t for t, _ in self.hist}
        rows = np.array([r for r in rows if int(panel.t[r]) not in known], dtype=int)
        if len(rows):
            Xr = panel.X[rows].reshape(-1, F)
            ok = np.isfinite(Xr).all(axis=1)
            pr = np.full(len(Xr), np.nan)
            with threadpool_limits(4):
                if ok.any():
                    pr[ok] = self.model.predict(Xr[ok])
            pr = pr.reshape(len(rows), S)
            for r, p in zip(rows, pr):
                self.hist.append((int(panel.t[r]), p))
        last_t, last_p = self.hist[-1]
        prev = np.concatenate([p for t, p in self.hist if t < last_t][-THRESHOLD_LOOKBACK:] or [np.array([np.nan])])
        prev = prev[np.isfinite(prev)]
        thr = float(np.quantile(prev, THRESHOLD_Q)) if len(prev) > 500 else float("inf")
        regime = bool(btc_regime_on(self.store, np.array([last_t + TF]))[0])
        row = int(np.where(panel.t == last_t)[0][0])
        out = {}
        order = np.argsort(-np.nan_to_num(last_p, nan=-9))
        for rank, j in enumerate(order):
            if not np.isfinite(last_p[j]):
                continue
            dv = float(panel.dvol[row, j])
            out[panel.symbols[j]] = {"pred": float(last_p[j]), "thr": thr, "regime": regime, "dvol": dv,
                                     "rank": rank + 1, "bar_t": last_t, "reasons": _reasons(panel, row, j)}
        self.preds = {last_t: out}
        self.latest = {"bar_t": last_t, "thr": thr, "regime": regime}

    def to_json(self) -> dict:
        m = self.model
        cur = self.preds[max(self.preds)] if self.preds else {}
        return {
            "state": self.state, "status": self.status_text, "ready": self.ready,
            "model": None if m is None else {"trained_at": m.trained_at, "samples": m.n_samples,
                                             "features": len(m.feature_names), "cutoff": m.cutoff, **m.info},
            "bar_t": self.latest["bar_t"] if self.latest else None,
            "threshold": self.latest["thr"] if self.latest else None,
            "regime": self.latest["regime"] if self.latest else None,
            "predictions": sorted(({"symbol": s, **{k: v for k, v in d.items() if k != "bar_t"}}
                                   for s, d in cur.items()), key=lambda x: -x["pred"]),
            "track_record": self.track_record(),
        }


class OracleReplay(_Base):
    """Backtest twin: serves the saved walk-forward (out-of-sample) predictions."""

    def __init__(self, store: HistoryStore, oos_path: Path):
        super().__init__()
        d = np.load(oos_path)
        self.t = d["t"].astype(np.int64)
        P = d["P"].astype(float)
        syms = [str(x) for x in d["symbols"]]
        panel = build_panel(store, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON)
        assert len(panel.t) >= len(self.t) and panel.t[0] == self.t[0]
        T = len(self.t)
        regime = btc_regime_on(store, self.t + TF)
        thr = np.full(T, np.inf)
        for i in range(THRESHOLD_LOOKBACK, T, 24):
            w = P[i - THRESHOLD_LOOKBACK:i].ravel()
            w = w[np.isfinite(w)]
            if len(w) > 500:
                thr[i:i + 24] = np.quantile(w, THRESHOLD_Q)
        self.index = {int(t): i for i, t in enumerate(self.t)}
        self.P, self.thr, self.regime, self.syms, self.panel = P, thr, regime, syms, panel
        self.ready = True
        self.status_text = "Replaying walk-forward forecasts"

    def prediction(self, symbol: str, bar_t: int | None = None) -> dict | None:
        i = self.index.get(int(bar_t)) if bar_t is not None else None
        if i is None or symbol not in self.syms:
            return None
        j = self.syms.index(symbol)
        p = self.P[i, j]
        if not np.isfinite(p):
            return None
        return {"pred": float(p), "thr": float(self.thr[i]), "regime": bool(self.regime[i]),
                "dvol": float(self.panel.dvol[i, j]), "reasons": _reasons(self.panel, i, j)}
