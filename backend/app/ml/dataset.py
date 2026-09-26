"""Assemble the (time x coin x feature) panel used for training and backtests."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data import TF, HistoryStore
from .features import HORIZON, PANEL_NAMES, SL_MULT, TP_MULT, base_features, panel_features, triple_barrier


@dataclass
class Panel:
    symbols: list[str]
    t: np.ndarray            # (T,) bar start times (hourly)
    X: np.ndarray            # (T, S, F) float32
    names: list[str]
    y: np.ndarray            # (T, S) 1/0/NaN  - take-profit hit first
    ret: np.ndarray          # (T, S) exit return (fraction), NaN if unknown
    held: np.ndarray         # (T, S) bars held
    dvol: np.ndarray         # (T, S) daily volatility estimate
    close: np.ndarray        # (T, S)
    tp_mult: float = TP_MULT
    sl_mult: float = SL_MULT
    horizon: int = HORIZON

    @property
    def decision_time(self) -> np.ndarray:
        """Features at row i are known at the CLOSE of bar i."""
        return self.t + TF

    def valid(self) -> np.ndarray:
        return np.isfinite(self.X).all(axis=2)


def build_panel(store: HistoryStore, until: float | None = None, with_labels: bool = True,
                tp_mult: float = TP_MULT, sl_mult: float = SL_MULT, horizon: int = HORIZON) -> Panel:
    symbols = store.symbols
    segs = {s: store.series(s, until) for s in symbols}
    t0 = min(int(segs[s][0].t[0]) for s in symbols if segs[s])
    t1 = max(int(segs[s][-1].t[-1]) for s in symbols if segs[s])
    t = np.arange(t0, t1 + TF, TF, dtype=np.int64)
    T, S = len(t), len(symbols)
    names: list[str] | None = None
    X = None
    shape2 = (T, S)
    y = np.full(shape2, np.nan)
    ret = np.full(shape2, np.nan)
    held = np.full(shape2, np.nan)
    raw_all = {k: np.full(shape2, np.nan) for k in ("ret_24", "ret_168", "ret_720", "dvol", "above_ema200", "close")}
    for j, s in enumerate(symbols):
        for sg in segs[s]:
            nm, Xs, raw = base_features(sg)
            if X is None:
                names = nm
                X = np.full((T, S, len(nm)), np.nan, dtype=np.float32)
            idx = ((sg.t - t0) // TF).astype(np.int64)
            X[idx, j] = Xs
            for k in raw_all:
                raw_all[k][idx, j] = raw[k]
            if with_labels:
                yy, rr, hh, _ = triple_barrier(sg, raw["dvol"], horizon, tp_mult, sl_mult)
                y[idx, j], ret[idx, j], held[idx, j] = yy, rr, hh
    assert X is not None and names is not None
    btc = symbols.index("BTC-USD")
    P = panel_features(names, X, raw_all, btc)
    full = np.concatenate([X, P], axis=2)
    return Panel(symbols, t, full, names + PANEL_NAMES, y, ret, held, raw_all["dvol"], raw_all["close"],
                 tp_mult, sl_mult, horizon)
