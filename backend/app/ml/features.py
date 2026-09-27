"""ORACLE features and labels.

Every feature at bar i uses ONLY data up to and including the close of bar i
(no look-ahead). The same functions run offline (full history) and live (last
~1,200 bars), so training and live inference see identical inputs.

Label ("triple barrier"): enter long at the close of bar i with
  take-profit  = entry * (1 + TP_MULT * daily_vol)
  stop-loss    = entry * (1 - SL_MULT * daily_vol)
  time limit   = HORIZON hours
y = 1 if the take-profit is touched before the stop (stop wins ties: conservative).
"""
from __future__ import annotations

import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.signal import lfilter

from .data import Series

TP_MULT = 2.0
SL_MULT = 1.0
HORIZON = 72
WARMUP = 760          # bars dropped at the start of every segment
LIVE_WINDOW = 1200    # bars used for live inference
EPS = 1e-12


# ----------------------------------------------------------------- primitives
def ema(x: np.ndarray, n: float) -> np.ndarray:
    a = 2.0 / (n + 1.0)
    y, _ = lfilter([a], [1.0, a - 1.0], x, zi=[(1.0 - a) * x[0]])
    return y


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    a = 1.0 / n
    y, _ = lfilter([a], [1.0, a - 1.0], x, zi=[(1.0 - a) * x[0]])
    return y


def roll_mean(x: np.ndarray, n: int) -> np.ndarray:
    """Trailing mean; windows containing NaN give NaN (NaNs don't poison later windows)."""
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        bad = np.isnan(x)
        c = np.cumsum(np.insert(np.where(bad, 0.0, x), 0, 0.0))
        nb = np.cumsum(np.insert(bad.astype(np.int64), 0, 0))
        w = (c[n:] - c[:-n]) / n
        w[(nb[n:] - nb[:-n]) > 0] = np.nan
        out[n - 1:] = w
    return out


def roll_std(x: np.ndarray, n: int) -> np.ndarray:
    m = roll_mean(x, n)
    m2 = roll_mean(x * x, n)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def roll_max(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def roll_min(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def lag(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k]
    return out


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up = wilder(np.clip(d, 0, None), n)
    dn = wilder(np.clip(-d, 0, None), n)
    return 100 - 100 / (1 + up / np.maximum(dn, EPS))


def adx(h, l, c, n):
    upm = np.diff(h, prepend=h[0])
    dnm = -np.diff(l, prepend=l[0])
    pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0)
    mdm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = np.maximum(wilder(tr, n), EPS)
    pdi = 100 * wilder(pdm, n) / atr
    mdi = 100 * wilder(mdm, n) / atr
    dx = 100 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, EPS)
    return wilder(dx, n), pdi, mdi, atr


# ------------------------------------------------------------- per-coin base
MOM_LAGS = (1, 3, 6, 12, 24, 72, 168, 336, 720)


def base_features(s: Series) -> tuple[list[str], np.ndarray, dict[str, np.ndarray]]:
    o, h, l, c, v, t = s.o, s.h, s.l, s.c, s.v, s.t
    n = len(c)
    lc = np.log(c)
    lr = np.diff(lc, prepend=lc[0])
    vol168 = roll_std(lr, 168)
    vol24 = roll_std(lr, 24)
    vol720 = roll_std(lr, 720)
    dvol = vol168 * np.sqrt(24)
    f: dict[str, np.ndarray] = {}

    raw_ret = {}
    for k in MOM_LAGS:
        r = lc - lag(lc, k)
        raw_ret[k] = r
        f[f"zret_{k}"] = r / (vol168 * np.sqrt(k) + EPS)
    f["vol_24_168"] = np.log((vol24 + EPS) / (vol168 + EPS))
    f["vol_168_720"] = np.log((vol168 + EPS) / (vol720 + EPS))
    f["log_dvol"] = np.log(dvol + EPS)

    ax14, pdi14, mdi14, atr14 = adx(h, l, c, 14)
    ax56, pdi56, mdi56, _ = adx(h, l, c, 56)
    f["atr_vs_vol"] = (atr14 / c) / (vol168 + EPS)
    f["rsi14"] = (rsi(c, 14) - 50) / 50
    f["rsi56"] = (rsi(c, 56) - 50) / 50
    f["adx14"] = ax14 / 100
    f["di14"] = (pdi14 - mdi14) / 100
    f["adx56"] = ax56 / 100
    f["di56"] = (pdi56 - mdi56) / 100

    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    for name, e in (("20", e20), ("50", e50), ("200", e200)):
        f[f"dist_ema{name}"] = np.log(c / e) / (dvol + EPS)
    f["ema20_50"] = np.log(e20 / e50) / (dvol + EPS)
    f["ema50_200"] = np.log(e50 / e200) / (dvol + EPS)
    f["slope_ema50"] = (np.log(e50) - lag(np.log(e50), 24)) / (dvol + EPS)
    f["slope_ema200"] = (np.log(e200) - lag(np.log(e200), 72)) / (dvol + EPS)

    mid = roll_mean(c, 20)
    sd = roll_std(c, 20)
    f["bb_pctb"] = np.clip((c - (mid - 2 * sd)) / (4 * sd + EPS), -1, 2)
    width = 4 * sd / (mid + EPS)
    f["bb_squeeze"] = np.log((width + EPS) / (roll_mean(width, 480) + EPS))

    for k in (24, 168, 720):
        hi, lo = roll_max(h, k), roll_min(l, k)
        f[f"donch_{k}"] = (c - lo) / (hi - lo + EPS)
    f["dd_720"] = np.log(c / roll_max(h, 720)) / (dvol + EPS)
    f["rally_720"] = np.log(c / roll_min(l, 720)) / (dvol + EPS)

    lv = np.log(v + 1e-9)
    f["vol_z"] = lv - np.log(roll_mean(v, 168) + 1e-9)
    f["vol_24_168r"] = np.log((roll_mean(v, 24) + 1e-9) / (roll_mean(v, 168) + 1e-9))
    f["log_dollar_vol"] = np.log(roll_mean(v * c, 168) + 1.0)
    f["up_ratio_24"] = roll_mean((lr > 0).astype(float), 24)
    f["max_ret_24"] = roll_max(lr, 24) / (vol168 + EPS)
    f["min_ret_24"] = roll_min(lr, 24) / (vol168 + EPS)
    m3 = roll_mean(lr ** 3, 168)
    f["skew_168"] = m3 / (vol168 ** 3 + EPS)

    rng = h - l + EPS
    f["body"] = (c - o) / rng
    f["upper_wick"] = (h - np.maximum(o, c)) / rng
    f["lower_wick"] = (np.minimum(o, c) - l) / rng
    f["range_vs_vol"] = (h - l) / c / (vol168 + EPS)

    close_t = t + 3600
    hour = (close_t // 3600) % 24
    dow = (close_t // 86400 + 3) % 7  # 1970-01-01 was a Thursday
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    f["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    names = list(f)
    X = np.column_stack([f[k] for k in names]).astype(np.float32)
    X[: min(WARMUP, n)] = np.nan
    raw = {"ret_24": raw_ret[24], "ret_168": raw_ret[168], "ret_720": raw_ret[720], "dvol": dvol,
           "above_ema200": (c > e200).astype(float), "atr": atr14, "close": c}
    for k in raw:
        raw[k] = raw[k].copy()
        raw[k][: min(WARMUP, n)] = np.nan
    return names, X, raw


# --------------------------------------------------------------- cross-asset
BTC_COLS = ("zret_24", "zret_168", "dist_ema200", "vol_24_168", "adx14", "donch_720", "rsi14", "slope_ema200")
PANEL_NAMES = [f"btc_{k}" for k in BTC_COLS] + [
    "rel_ret_24", "rel_ret_168", "cs_rank_24", "cs_rank_168", "cs_rank_720",
    "breadth_ema200", "breadth_mom_168", "mkt_zret_24",
]


def _cs_rank(M: np.ndarray) -> np.ndarray:
    """Rank across coins per row, scaled 0..1, NaN-aware."""
    out = np.full(M.shape, np.nan)
    valid = ~np.isnan(M)
    cnt = valid.sum(axis=1)
    order = np.argsort(np.where(valid, M, np.inf), axis=1)
    ranks = np.empty_like(order, dtype=float)
    rows = np.arange(M.shape[0])[:, None]
    ranks[rows, order] = np.arange(M.shape[1])[None, :]
    ok = cnt > 1
    out[ok] = ranks[ok] / (cnt[ok, None] - 1)
    out[~valid] = np.nan
    return out


def panel_features(base_names: list[str], X: np.ndarray, raw: dict[str, np.ndarray], btc_idx: int) -> np.ndarray:
    """X: (T, S, F) base features; raw arrays (T, S). Returns (T, S, P)."""
    T, S, _ = X.shape
    col = {k: i for i, k in enumerate(base_names)}
    P = np.full((T, S, len(PANEL_NAMES)), np.nan, dtype=np.float32)
    for j, k in enumerate(BTC_COLS):
        P[:, :, j] = X[:, btc_idx, col[k]][:, None]
    dv = raw["dvol"]
    j = len(BTC_COLS)
    P[:, :, j] = (raw["ret_24"] - raw["ret_24"][:, [btc_idx]]) / (dv + EPS)
    P[:, :, j + 1] = (raw["ret_168"] - raw["ret_168"][:, [btc_idx]]) / (dv * np.sqrt(7) + EPS)
    P[:, :, j + 2] = _cs_rank(raw["ret_24"] / (dv + EPS))
    P[:, :, j + 3] = _cs_rank(raw["ret_168"] / (dv + EPS))
    P[:, :, j + 4] = _cs_rank(raw["ret_720"] / (dv + EPS))
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # rows where no coin has data yet
        P[:, :, j + 5] = np.nanmean(raw["above_ema200"], axis=1)[:, None]
        P[:, :, j + 6] = np.nanmean((raw["ret_168"] > 0).astype(float) + np.where(np.isnan(raw["ret_168"]), np.nan, 0), axis=1)[:, None]
        P[:, :, j + 7] = np.nanmean(X[:, :, col["zret_24"]], axis=1)[:, None]
    P[np.isnan(X[:, :, 0])] = np.nan
    return P


# -------------------------------------------------------------------- labels
def triple_barrier(s: Series, dvol: np.ndarray, horizon: int = HORIZON,
                   tp_mult: float = TP_MULT, sl_mult: float = SL_MULT, side: str = "long"):
    """Returns y (1/0, NaN if unknown), exit return (fraction), bars held.

    side="short" mirrors the trade: profit when price falls (returns are the SHORT's returns)."""
    c, h, l = s.c, s.h, s.l
    if side == "short":  # mirror prices: a falling market becomes a rising one
        c, h, l = 1.0 / c, 1.0 / l, 1.0 / h
    n = len(c)
    tp = c * (1 + tp_mult * dvol)
    sl = c * (1 - sl_mult * dvol)
    y = np.full(n, np.nan)
    ret = np.full(n, np.nan)
    held = np.full(n, np.nan)
    valid = np.arange(n) + horizon < n
    hit_tp = np.full(n, horizon + 1)
    hit_sl = np.full(n, horizon + 1)
    for k in range(horizon, 0, -1):  # earliest hit wins
        hk = lag_forward(h, k)
        lk = lag_forward(l, k)
        hit_tp = np.where(hk >= tp, k, hit_tp)
        hit_sl = np.where(lk <= sl, k, hit_sl)
    win = hit_tp < hit_sl
    loss = hit_sl <= hit_tp
    loss &= hit_sl <= horizon
    timeout = ~win & ~loss
    y[valid] = win[valid].astype(float)
    exit_c = lag_forward(c, horizon)
    ret = np.where(win, tp / c - 1, np.where(loss, sl / c - 1, exit_c / c - 1))
    held = np.where(win, hit_tp, np.where(loss, hit_sl, horizon)).astype(float)
    ret[~valid] = np.nan
    held[~valid] = np.nan
    bad = np.isnan(dvol)
    y[bad] = np.nan
    ret[bad] = np.nan
    return y, ret, held, timeout


def lag_forward(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[:-k] = x[k:]
    return out
