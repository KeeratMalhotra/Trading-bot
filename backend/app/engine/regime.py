"""Market regime detection: what kind of market are we in right now?"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .indicators import Ind


@dataclass
class Regime:
    name: str      # TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE
    trend: str     # higher-timeframe bias: up | down | flat
    adx: float
    vol_rank: float  # ATR% percentile vs last 100 bars (0..1)

    @property
    def label(self) -> str:
        return {"TRENDING_UP": "Uptrend", "TRENDING_DOWN": "Downtrend",
                "RANGING": "Ranging", "VOLATILE": "Volatile"}[self.name]


def classify(sig: Ind, trend: Ind) -> Regime:
    tc = trend.close
    e50, e200 = float(trend.ema50[-1]), float(trend.ema200[-1])
    slope = float(trend.ema50[-1] / trend.ema50[-6] - 1) if trend.n > 6 else 0.0
    if tc > e50 > e200 and slope > 0:
        bias = "up"
    elif tc < e50 < e200 and slope < 0:
        bias = "down"
    else:
        bias = "flat"

    a = float(sig.adx[-1])
    atr_pct = sig.atr / np.maximum(sig.c, 1e-12)
    window = atr_pct[-100:]
    vol_rank = float((window < atr_pct[-1]).mean())

    if vol_rank > 0.92 and a < 25:
        name = "VOLATILE"
    elif a >= 22 and sig.pdi[-1] > sig.mdi[-1] and bias != "down":
        name = "TRENDING_UP"
    elif a >= 22 and sig.mdi[-1] > sig.pdi[-1] and bias != "up":
        name = "TRENDING_DOWN"
    else:
        name = "RANGING"
    return Regime(name=name, trend=bias, adx=a, vol_rank=vol_rank)
