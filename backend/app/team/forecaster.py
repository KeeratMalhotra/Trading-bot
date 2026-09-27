"""ORACLE's brain for the team: long AND short forecasts for every coin, every hour.

Live:   downloads/updates hourly history + funding + Fear & Greed, trains a long model and a
        short model (retrained daily), and keeps an out-of-sample forecast history so the
        desk can judge ORACLE fairly. On first start it rebuilds the last ~150 days of
        forecasts walk-forward (models that only saw data before each window).
Replay: serves saved walk-forward forecasts (app.ml.research output).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np

from ..config import DATA_DIR
from ..ml.altdata import AltData
from ..ml.data import TF, HistoryStore
from ..ml.dataset import build_panel
from ..ml.oracle import HORIZON, SL_MULT, THRESHOLD_LOOKBACK, THRESHOLD_Q, TP_MULT, _reasons

log = logging.getLogger("team.forecaster")
SIDES = ("long", "short")
BOOTSTRAP_DAYS = 240   # enough history for NOVA's and the desk's 90-day look-backs
WINDOW_DAYS = 60
RETRAIN_EVERY = 24 * 3600


class ForecastBase:
    ready = False
    state = "starting"

    def __init__(self):
        self.P: dict[str, np.ndarray] = {}      # side -> (T, S) forecasts aligned to self.t
        self.t = np.zeros(0, dtype=np.int64)
        self.symbols: list[str] = []
        self.dvol = np.zeros((0, 0))
        self.panel_long = None
        self.panels: dict = {}

    def _index(self, bar_t: int) -> int | None:
        i = int(np.searchsorted(self.t, bar_t))
        return i if i < len(self.t) and self.t[i] == bar_t else None

    def threshold(self, side: str, bar_t: int) -> float:
        i = self._index(bar_t)
        if i is None or side not in self.P:
            return float("inf")
        w = self.P[side][max(0, i - THRESHOLD_LOOKBACK):i].ravel()
        w = w[np.isfinite(w)]
        return float(np.quantile(w, THRESHOLD_Q)) if len(w) > 500 else float("inf")

    def forecast(self, symbol: str, bar_t: int) -> dict | None:
        i = self._index(bar_t)
        if i is None or symbol not in self.symbols:
            return None
        j = self.symbols.index(symbol)
        out = {"dvol": float(self.dvol[i, j])}
        for side in SIDES:
            p = self.P.get(side)
            out[side] = float(p[i, j]) if p is not None and np.isfinite(p[i, j]) else None
        if out["long"] is None and out["short"] is None:
            return None
        if self.panel_long is not None:
            out["reasons"] = _reasons(self.panel_long, i, j)
        return out

    def latest_bar(self, now: float | None = None) -> int | None:
        """Most recent bar with forecasts (and, in replay, whose hour has closed by `now`)."""
        if not len(self.t) or "long" not in self.P:
            return None
        if now is not None and self.state == "replay":
            b = int(now // TF * TF) - TF
            return b if self._index(b) is not None else None
        ok = np.where(np.isfinite(self.P["long"]).any(axis=1))[0]
        return int(self.t[ok[-1]]) if len(ok) else None


class LiveForecaster(ForecastBase):
    def __init__(self, store: HistoryStore, alt: AltData, root: Path | None = None):
        super().__init__()
        self.store = store
        self.alt = alt
        self.root = root or DATA_DIR
        self.models: dict[str, object] = {}
        self.trained_at = 0.0
        self.error = ""
        self.cycle = 0
        self.info: dict = {}
        self.on_update = None     # callback(bar_t) after each forecast refresh

    @property
    def ready(self) -> bool:  # type: ignore[override]
        return bool(self.models) and self.latest_bar() is not None

    def status_text(self) -> str:
        return {"downloading": f"Downloading market history ({self.store.progress * 100:.0f}%)",
                "training": "Training forecast models", "bootstrap": "Training models and rebuilding forecast history",
                "funding": "Downloading funding-rate history",
                "error": f"Offline: {self.error}"}.get(self.state, "Forecasting" if self.ready else "Starting")

    async def run(self, stop: asyncio.Event) -> None:
        self.store.load()
        self.alt.load()
        while not stop.is_set():
            try:
                self.state = "downloading" if not any(len(a) for a in self.store.raw.values()) else "updating"
                await self.store.update()
                if not any(len(a) for a in self.alt.funding.values()):
                    self.state = "funding"
                await self.alt.update()
                if not self.models or time.time() - self.trained_at > RETRAIN_EVERY:
                    self.state = "bootstrap" if not self.models else "training"
                    await asyncio.to_thread(self._train_and_bootstrap)
                await asyncio.to_thread(self._forecast_latest)
                self.state = "ready"
                self.cycle += 1
                if self.on_update:
                    self.on_update(self.latest_bar())
            except Exception as e:  # noqa: BLE001
                log.exception("forecaster cycle failed")
                self.state, self.error = "error", str(e)[:120]
            now = time.time()
            nxt = (now // TF + 1) * TF + 100
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(30.0, nxt - now))
            except asyncio.TimeoutError:
                pass

    def _panels(self):
        return {side: build_panel(self.store, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON, side=side)
                for side in SIDES}

    def _train_and_bootstrap(self) -> None:
        from threadpoolctl import threadpool_limits

        from ..ml.model import fit
        t0 = time.time()
        with threadpool_limits(4):
            panels = self._panels()
            now = time.time()
            first = not self.P
            T = len(panels["long"].t)
            for side, panel in panels.items():
                P = np.full(panel.y.shape, np.nan, dtype=np.float32)
                if not first and side in self.P:           # keep the forecast history we already have
                    k = min(len(self.P[side]), T)
                    P[:k] = self.P[side][:k]
                if first:                                   # walk-forward rebuild of recent history
                    start = now - BOOTSTRAP_DAYS * 86400
                    cut = start
                    while cut < now - 3600:
                        m = fit(panel, cut, kind="reg")
                        nxt = min(cut + WINDOW_DAYS * 86400, now)
                        rows = np.where((panel.decision_time >= cut) & (panel.decision_time < nxt))[0]
                        self._fill(P, panel, rows, m)
                        cut = nxt
                self.models[side] = fit(panel, now, kind="reg")
                self.P[side] = P
            self.t = panels["long"].t
            self.symbols = panels["long"].symbols
            self.dvol = panels["long"].dvol
            self.panel_long = panels["long"]
            self.panels = panels
        self.trained_at = time.time()
        self.info = {"train_seconds": round(time.time() - t0, 1), "samples": self.models["long"].n_samples,
                     "features": len(self.models["long"].feature_names), "hours": int(T)}
        log.info("forecaster trained (%s)", self.info)

    @staticmethod
    def _fill(P, panel, rows, model) -> None:
        if not len(rows):
            return
        Xr = panel.X[rows].reshape(-1, panel.X.shape[2])
        ok = np.isfinite(Xr).all(axis=1)
        pr = np.full(len(Xr), np.nan, dtype=np.float32)
        if ok.any():
            pr[ok] = model.predict(Xr[ok])
        P[rows] = pr.reshape(len(rows), panel.X.shape[1])

    def _forecast_latest(self) -> None:
        from threadpoolctl import threadpool_limits
        with threadpool_limits(4):
            panels = self._panels()
            T = len(panels["long"].t)
            for side, panel in panels.items():
                old = self.P.get(side, np.zeros((0, len(panel.symbols)), dtype=np.float32))
                P = np.full(panel.y.shape, np.nan, dtype=np.float32)
                k = min(len(old), T)
                P[:k] = old[:k]
                todo = np.array([r for r in range(max(0, T - 72), T) if not np.isfinite(P[r]).any()], dtype=int)
                self._fill(P, panel, todo, self.models[side])
                self.P[side] = P
            self.t = panels["long"].t
            self.dvol = panels["long"].dvol
            self.panel_long = panels["long"]
            self.panels = panels

    def to_json(self) -> dict:
        bar = self.latest_bar()
        rows = []
        if bar is not None:
            for s in self.symbols:
                f = self.forecast(s, bar)
                if f:
                    rows.append({"symbol": s, "long": f["long"], "short": f["short"], "dvol": f["dvol"],
                                 "reasons": f.get("reasons", [])})
        return {"state": self.state, "status": self.status_text(), "ready": self.ready, "bar_t": bar,
                "thr_long": self.threshold("long", bar) if bar else None,
                "thr_short": self.threshold("short", bar) if bar else None,
                "trained_at": self.trained_at or None, "model": self.info, "forecasts": rows}


class ReplayForecaster(ForecastBase):
    def __init__(self, store: HistoryStore):
        super().__init__()
        from .research import load_oos
        self.panel_long = build_panel(store, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON, side="long")
        self.t = self.panel_long.t
        self.symbols = self.panel_long.symbols
        self.dvol = self.panel_long.dvol
        for side in SIDES:
            P = load_oos(side)
            full = np.full((len(self.t), len(self.symbols)), np.nan)
            full[:min(len(P), len(self.t))] = P[:len(self.t)]
            self.P[side] = full
        self._thr = {side: self._thr_series(self.P[side]) for side in SIDES}
        self.state = "replay"

    @staticmethod
    def _thr_series(P):
        from .research import oracle_thresholds
        return oracle_thresholds(P)

    @property
    def ready(self) -> bool:  # type: ignore[override]
        return True

    def threshold(self, side: str, bar_t: int) -> float:
        i = self._index(bar_t)
        return float(self._thr[side][i]) if i is not None else float("inf")
