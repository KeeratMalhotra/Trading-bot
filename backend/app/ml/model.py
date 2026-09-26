"""ORACLE's model: gradient-boosted trees predicting P(take-profit before stop)."""
from __future__ import annotations

import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .data import TF
from .dataset import Panel

EMBARGO = 24 * 3600
TRAIN_STRIDE = 3  # hours between training samples (labels overlap heavily otherwise)


def make_model(seed: int = 7, kind: str = "clf"):
    if kind == "reg":
        return HistGradientBoostingRegressor(
            loss="squared_error", learning_rate=0.04, max_iter=250, max_leaf_nodes=15,
            min_samples_leaf=400, l2_regularization=1.0, max_features=0.7,
            early_stopping=False, random_state=seed,
        )
    return HistGradientBoostingClassifier(
        loss="log_loss", learning_rate=0.04, max_iter=250, max_leaf_nodes=15,
        min_samples_leaf=400, l2_regularization=1.0, max_features=0.7,
        early_stopping=False, random_state=seed,
    )


@dataclass
class OracleModel:
    models: list
    feature_names: list[str]
    cutoff: float                 # no label information after this time was used
    n_samples: int
    base_rate: float
    trained_at: float = field(default_factory=time.time)
    info: dict = field(default_factory=dict)
    kind: str = "clf"   # clf: P(take-profit first) | reg: expected outcome in R (gross of fees)

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.asarray(X, dtype=np.float32)
        if self.kind == "reg":
            return np.mean([m.predict(X) for m in self.models], axis=0)
        return np.mean([m.predict_proba(X)[:, 1] for m in self.models], axis=0)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(self, f)
        tmp.replace(path)

    @staticmethod
    def load(path: Path) -> "OracleModel | None":
        try:
            with open(path, "rb") as f:
                m = pickle.load(f)
            return m if isinstance(m, OracleModel) else None
        except Exception:  # noqa: BLE001
            return None


def training_rows(panel: Panel, cutoff: float, start: float | None = None,
                  kind: str = "clf") -> tuple[np.ndarray, np.ndarray]:
    """Rows whose label was fully known `EMBARGO` before `cutoff` (purged + embargoed)."""
    label_end = panel.decision_time + panel.horizon * TF
    ok_t = label_end <= cutoff - EMBARGO
    if start is not None:
        ok_t &= panel.t >= start
    rows = np.where(ok_t)[0]
    rows = rows[rows % TRAIN_STRIDE == 0]
    Xr = panel.X[rows].reshape(-1, panel.X.shape[2])
    if kind == "reg":
        r = panel.ret[rows] / (panel.sl_mult * panel.dvol[rows])
        yr = np.clip(r, -1.3, panel.tp_mult / panel.sl_mult + 0.3).reshape(-1)
    else:
        yr = panel.y[rows].reshape(-1)
    good = np.isfinite(Xr).all(axis=1) & np.isfinite(yr)
    return Xr[good], yr[good]


def fit(panel: Panel, cutoff: float, seeds: tuple[int, ...] = (7, 11), kind: str = "clf") -> OracleModel:
    X, y = training_rows(panel, cutoff, kind=kind)
    models = []
    for sd in seeds:
        m = make_model(sd, kind)
        m.fit(X, y)
        models.append(m)
    return OracleModel(models, panel.names, cutoff, int(len(y)), float(y.mean()), kind=kind)
