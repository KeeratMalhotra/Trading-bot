"""Technical indicators (numpy) and a per-(symbol, timeframe) cached snapshot."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..market.candles import CandleSeries


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.empty_like(x)
    if len(x) == 0:
        return out
    a = 2.0 / (n + 1)
    prev = float(x[0])
    for i, v in enumerate(x.tolist()):
        prev = prev + a * (v - prev)
        out[i] = prev
    return out


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    out = np.empty_like(x)
    if len(x) == 0:
        return out
    prev = float(np.mean(x[:n])) if len(x) >= n else float(x[0])
    for i, v in enumerate(x.tolist()):
        if i < n:
            out[i] = prev
            continue
        prev = prev + (v - prev) / n
        out[i] = prev
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) < n:
        return np.full_like(x, np.mean(x) if len(x) else 0.0)
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.empty_like(x)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    out[:n - 1] = out[n - 1]
    return out


def rolling_std(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) < n:
        return np.full_like(x, np.std(x) if len(x) else 0.0)
    w = np.lib.stride_tricks.sliding_window_view(x, n)
    out = np.empty_like(x)
    out[n - 1:] = w.std(axis=1)
    out[:n - 1] = out[n - 1]
    return out


def true_range(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up = wilder(np.clip(d, 0, None), n)
    dn = wilder(np.clip(-d, 0, None), n)
    rs = np.divide(up, dn, out=np.full_like(up, 100.0), where=dn > 1e-12)
    return 100 - 100 / (1 + rs)


def adx(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14):
    upm = np.diff(h, prepend=h[0])
    dnm = -np.diff(l, prepend=l[0])
    plus_dm = np.where((upm > dnm) & (upm > 0), upm, 0.0)
    minus_dm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    tr = wilder(true_range(h, l, c), n)
    tr = np.where(tr <= 1e-12, 1e-12, tr)
    pdi = 100 * wilder(plus_dm, n) / tr
    mdi = 100 * wilder(minus_dm, n) / tr
    dx = 100 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    return wilder(dx, n), pdi, mdi


@dataclass
class Ind:
    """Indicator snapshot of CLOSED candles. Index -1 = last closed bar."""
    symbol: str
    tf: int
    n: int
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    ema20: np.ndarray
    ema50: np.ndarray
    ema200: np.ndarray
    rsi: np.ndarray
    atr: np.ndarray
    adx: np.ndarray
    pdi: np.ndarray
    mdi: np.ndarray
    bb_mid: np.ndarray
    bb_up: np.ndarray
    bb_lo: np.ndarray
    bb_width: np.ndarray
    vol_sma: np.ndarray

    @property
    def close(self) -> float:
        return float(self.c[-1])

    @property
    def atr_now(self) -> float:
        return float(self.atr[-1])

    def pct_rank(self, arr: np.ndarray, lookback: int = 100, idx: int = -1) -> float:
        window = arr[-lookback:] if idx == -1 else arr[-lookback + idx + 1: idx + 1]
        if len(window) == 0:
            return 0.5
        return float((window < arr[idx]).mean())


MIN_BARS = 60
LOOKBACK = 320  # bars used for indicator computation


def compute(symbol: str, series: CandleSeries) -> Ind | None:
    a = series.arrays()
    if len(a["c"]) < MIN_BARS:
        return None
    sl = slice(-LOOKBACK, None)
    t, o, h, l, c, v = (a[k][sl] for k in ("t", "o", "h", "l", "c", "v"))
    mid = sma(c, 20)
    sd = rolling_std(c, 20)
    ax, pdi, mdi = adx(h, l, c, 14)
    return Ind(
        symbol=symbol, tf=series.tf, n=len(c), t=t, o=o, h=h, l=l, c=c, v=v,
        ema20=ema(c, 20), ema50=ema(c, 50), ema200=ema(c, 200), rsi=rsi(c, 14),
        atr=wilder(true_range(h, l, c), 14), adx=ax, pdi=pdi, mdi=mdi,
        bb_mid=mid, bb_up=mid + 2 * sd, bb_lo=mid - 2 * sd,
        bb_width=np.divide(4 * sd, mid, out=np.zeros_like(mid), where=mid > 0),
        vol_sma=sma(v, 20),
    )


class IndicatorCache:
    """Shared across bots: indicators are recomputed only when a candle closes."""

    def __init__(self, hub):
        self.hub = hub
        self._cache: dict[tuple[str, int], tuple[int, Ind | None]] = {}

    def get(self, symbol: str, tf: int) -> Ind | None:
        series = self.hub.series[symbol][tf]
        key = (symbol, tf)
        hit = self._cache.get(key)
        if hit and hit[0] == series.version:
            return hit[1]
        ind = compute(symbol, series)
        self._cache[key] = (series.version, ind)
        return ind
