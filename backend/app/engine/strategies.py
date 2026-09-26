"""Long-only spot strategies. Each returns a Setup (trade idea) or a Watch (what it's waiting for).

All levels come from CLOSED candles, so signals never repaint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .indicators import Ind
from .regime import Regime


@dataclass
class Ctx:
    """What a strategy may look at besides its own coin: other markets, ORACLE, the clock."""
    cache: Any
    oracle: Any = None
    now: float = 0.0

    def ind(self, symbol: str, tf: int):
        return self.cache.get(symbol, tf)


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 10:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.3f}"
    return f"${p:,.5f}"


def coin(symbol: str) -> str:
    return symbol.split("-")[0]


@dataclass
class Setup:
    strategy: str
    symbol: str
    ref_price: float
    stop: float
    target: float | None      # None = use profile target_rr
    confidence: float
    headline: str
    reasons: list[str] = field(default_factory=list)
    atr: float = 0.0
    # optional per-strategy trade management (overrides the risk profile)
    target_rr: float | None = None
    trail_atr: float | None = None
    breakeven_r: float | None = None
    trail_start_r: float | None = None
    allow_partial: bool = True
    time_stop_bars: int | None = None   # hard exit after N signal bars, win or lose
    close_stop: bool = False            # stop is judged on CLOSED signal bars (daily systems)
    hard_stop: float | None = None      # intraday disaster stop when close_stop=True
    rank: float | None = None           # ordering when several setups compete (default: confidence)


@dataclass
class Watch:
    strategy: str
    symbol: str
    note: str
    interest: float  # 0..1, how close to a setup (used to pick what to talk about)


Result = Setup | Watch

STRATEGY_NAMES = {
    "trend_pullback": "Trend Pullback",
    "breakout": "Breakout",
    "mean_reversion": "Mean Reversion",
    "momentum": "Momentum",
    "trend_ride": "Trend Ride",
    "trend_follow": "Daily Trend",
    "oracle": "ML Forecast",
    "hodl": "Buy & Hold",
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _stop_bounds(price: float, stop: float, atr: float, lo_atr: float = 1.0, hi_atr: float = 3.0) -> float:
    dist = _clamp(price - stop, lo_atr * atr, hi_atr * atr)
    return price - dist


# --------------------------------------------------------------- trend pullback
def trend_pullback(sig: Ind, trend: Ind, rg: Regime, price: float, ctx=None) -> Result:
    s, c = sig.symbol, coin(sig.symbol)
    atr = sig.atr_now
    if rg.trend != "up":
        why = "downtrend" if rg.trend == "down" else "no clear trend"
        return Watch("trend_pullback", s, f"{c} higher timeframe shows {why}. Pullback buys need an uptrend.", 0.05)
    if rg.name != "TRENDING_UP":
        return Watch("trend_pullback", s, f"{c} big picture is up, but momentum is weak (ADX {rg.adx:.0f}). Waiting for the trend to strengthen.", 0.25)
    e20, e50 = float(sig.ema20[-1]), float(sig.ema50[-1])
    if not (e20 > e50 and sig.close > e50):
        return Watch("trend_pullback", s, f"{c} big trend is up but price is below the EMA50 ({fmt_price(e50)}). Too weak to buy yet.", 0.2)
    touched = any(sig.l[-k] <= sig.ema20[-k] + 0.3 * sig.atr[-k] for k in (1, 2, 3))
    bullish = sig.c[-1] > sig.o[-1] and sig.c[-1] > e20 and sig.c[-1] > sig.h[-2]
    r, rp = float(sig.rsi[-1]), float(sig.rsi[-2])
    if not touched:
        dist = (price - e20) / price * 100
        return Watch("trend_pullback", s,
                     f"{c} is in an uptrend and {dist:.1f}% above the EMA20 ({fmt_price(e20)}). Waiting for a pullback, not chasing.",
                     _clamp(0.6 - dist / 5, 0.2, 0.6))
    if not (bullish and 40 <= r <= 68 and r > rp):
        return Watch("trend_pullback", s,
                     f"{c} pulled back to the EMA20 in an uptrend. Waiting for a bullish close to confirm (RSI {r:.0f}).", 0.8)
    stop = _stop_bounds(price, float(np.min(sig.l[-5:])) - 0.25 * atr, atr)
    conf = 55 + _clamp(rg.adx - 20, 0, 20) * 0.8
    conf += 8 if rg.name == "TRENDING_UP" else 0
    conf += 5 if sig.v[-1] > sig.vol_sma[-1] else 0
    conf += 5 if 45 <= r <= 60 else 0
    return Setup("trend_pullback", s, sig.close, stop, None, _clamp(conf, 0, 95),
                 f"{c} bounced off the EMA20 in an uptrend",
                 [f"Higher timeframe trend is up (ADX {rg.adx:.0f})",
                  f"Pullback held the EMA20 at {fmt_price(e20)}",
                  f"RSI turning up: {rp:.0f} to {r:.0f}"], atr)


# --------------------------------------------------------------------- breakout
def breakout(sig: Ind, trend: Ind, rg: Regime, price: float, ctx=None) -> Result:
    s, c = sig.symbol, coin(sig.symbol)
    atr = sig.atr_now
    level = float(np.max(sig.h[-21:-1]))
    vol_ratio = float(sig.v[-1] / max(sig.vol_sma[-2], 1e-12))
    if rg.trend == "down":
        return Watch("breakout", s, f"{c} higher timeframe is in a downtrend. Breakouts there usually fail.", 0.05)
    if sig.close <= level:
        gap = (level - price) / price * 100
        if gap <= 0:
            return Watch("breakout", s, f"{c} is testing resistance at {fmt_price(level)} right now. "
                         "Needs a candle CLOSE above it, with volume, to count.", 0.75)
        return Watch("breakout", s, f"{c} resistance at {fmt_price(level)} is {gap:.2f}% away. Watching for a breakout with volume.",
                     _clamp(0.7 - gap / 2, 0.1, 0.7))
    if vol_ratio < 1.4:
        return Watch("breakout", s, f"{c} poked above {fmt_price(level)} but volume is only {vol_ratio:.1f}× normal. Likely a fake-out.", 0.6)
    if price - level > 1.0 * atr:
        return Watch("breakout", s, f"{c} broke out, but price is already too far past {fmt_price(level)}. Not chasing.", 0.4)
    widths = sig.bb_width[-52:-2]
    squeeze = len(widths) > 10 and sig.bb_width[-2] <= np.quantile(widths, 0.35)
    stop = _stop_bounds(price, max(level - 1.0 * atr, price - 2.5 * atr), atr, 1.0, 2.5)
    conf = 52 + min(vol_ratio - 1.4, 1.6) * 10 + (10 if squeeze else 0)
    conf += 8 if rg.trend == "up" else 0
    conf += _clamp(rg.adx - 18, 0, 15) * 0.5
    reasons = [f"Closed above 20-bar high {fmt_price(level)}", f"Volume {vol_ratio:.1f}× the average"]
    if squeeze:
        reasons.append("Volatility squeeze just released")
    return Setup("breakout", s, sig.close, stop, None, _clamp(conf, 0, 95),
                 f"{c} broke out above {fmt_price(level)} on {vol_ratio:.1f}× volume", reasons, atr)


# --------------------------------------------------------------- mean reversion
def mean_reversion(sig: Ind, trend: Ind, rg: Regime, price: float, ctx=None) -> Result:
    s, c = sig.symbol, coin(sig.symbol)
    atr = sig.atr_now
    if rg.name not in ("RANGING", "VOLATILE") or rg.adx >= 24:
        return Watch("mean_reversion", s, f"{c} is trending (ADX {rg.adx:.0f}). Mean reversion only works in ranges.", 0.05)
    if rg.trend == "down" and rg.name != "VOLATILE":
        return Watch("mean_reversion", s, f"{c} is ranging inside a bigger downtrend. Buying dips there is dangerous.", 0.1)
    lo_band = float(sig.bb_lo[-1])
    pierced = sig.l[-1] < sig.bb_lo[-1] or sig.l[-2] < sig.bb_lo[-2]
    min_rsi = float(np.min(sig.rsi[-3:]))
    if not pierced:
        gap = (price - lo_band) / price * 100
        return Watch("mean_reversion", s,
                     f"{c} is ranging between {fmt_price(float(sig.bb_lo[-1]))} and {fmt_price(float(sig.bb_up[-1]))}. Would buy near the lower band.",
                     _clamp(0.5 - gap / 4, 0.1, 0.5))
    if not (sig.c[-1] > lo_band and min_rsi < 33 and sig.rsi[-1] > sig.rsi[-2] and sig.c[-1] > sig.o[-1]):
        return Watch("mean_reversion", s, f"{c} dipped below the lower Bollinger Band. Waiting for buyers to step back in.", 0.8)
    stop = _stop_bounds(price, float(np.min(sig.l[-3:])) - 0.3 * atr, atr, 0.8, 2.5)
    target = float(sig.bb_mid[-1])
    conf = 55 + _clamp(33 - min_rsi, 0, 15) + (8 if rg.trend == "up" else 0) + (5 if rg.name == "RANGING" else 0)
    return Setup("mean_reversion", s, sig.close, stop, target, _clamp(conf, 0, 95),
                 f"{c} snapped back inside the lower Bollinger Band",
                 [f"Oversold: RSI bottomed at {min_rsi:.0f}", "Range-bound market (low ADX)",
                  f"Target: the middle of the range at {fmt_price(target)}"], atr)


# --------------------------------------------------------------------- momentum
def momentum(sig: Ind, trend: Ind, rg: Regime, price: float, ctx=None) -> Result:
    s, c = sig.symbol, coin(sig.symbol)
    atr = sig.atr_now
    if sig.n < 14:
        return Watch("momentum", s, f"{c} not enough data yet.", 0.0)
    roc = float(sig.c[-1] / sig.c[-13] - 1)
    atr_pct = atr / sig.close
    r = float(sig.rsi[-1])
    if rg.trend == "down" or roc <= 0:
        return Watch("momentum", s, f"{c} has no upside momentum ({roc * 100:+.1f}% over 12 bars).", 0.05)
    strong = roc > 3 * atr_pct
    if not (strong and sig.close > sig.ema20[-1] and 55 <= r <= 78
            and sig.c[-1] > sig.c[-2] > sig.c[-3] and sig.v[-1] > 1.2 * sig.vol_sma[-1]):
        return Watch("momentum", s, f"{c} is up {roc * 100:+.1f}% over 12 bars. Not explosive enough yet (RSI {r:.0f}).",
                     _clamp(roc / (3 * atr_pct) * 0.6, 0.05, 0.7))
    stop = price - 1.5 * atr
    conf = 52 + min(roc / atr_pct - 3, 4) * 4 + (8 if rg.adx > 25 else 0) + (5 if rg.trend == "up" else 0)
    return Setup("momentum", s, sig.close, stop, None, _clamp(conf, 0, 95),
                 f"{c} is ripping: {roc * 100:+.1f}% with rising volume",
                 [f"12-bar move is {roc / atr_pct:.1f}× its normal range", f"RSI {r:.0f}, strong but not exhausted",
                  "Three higher closes in a row"], atr)


# ------------------------------------------------------------------- trend ride
def trend_ride(sig: Ind, trend: Ind, rg: Regime, price: float, ctx=None) -> Result:
    """Classic trend following: join a fresh uptrend early, wide stop, ride it with a trailing stop."""
    s, c = sig.symbol, coin(sig.symbol)
    atr = sig.atr_now
    if rg.trend != "up":
        return Watch("trend_ride", s, f"{c} has no higher-timeframe uptrend to ride.", 0.05)
    e20, e50 = sig.ema20, sig.ema50
    crossed = any(e20[-k] > e50[-k] and e20[-k - 1] <= e50[-k - 1] for k in (1, 2, 3))
    if not crossed:
        if e20[-1] <= e50[-1]:
            gap = (e50[-1] - e20[-1]) / price * 100
            return Watch("trend_ride", s, f"{c} fast average is {gap:.2f}% below the slow one. A cross up would start a new trend leg.",
                         _clamp(0.6 - gap, 0.1, 0.6))
        return Watch("trend_ride", s, f"{c} trend leg already underway. Missed the start; waiting for a pullback instead.", 0.15)
    adx_rising = sig.adx[-1] > sig.adx[-4]
    if not (sig.close > e50[-1] and sig.adx[-1] >= 16 and adx_rising):
        return Watch("trend_ride", s, f"{c} averages just crossed up, but trend strength isn't building yet (ADX {rg.adx:.0f}).", 0.7)
    stop = _stop_bounds(price, price - 2.5 * atr, atr, 2.0, 3.5)
    conf = 58 + _clamp(rg.adx - 16, 0, 20) * 0.6 + (8 if trend.close > trend.ema20[-1] else 0) \
        + (5 if sig.v[-1] > sig.vol_sma[-1] else 0)
    return Setup("trend_ride", s, sig.close, stop, None, _clamp(conf, 0, 95),
                 f"{c} started a new uptrend leg (EMA20 crossed above EMA50)",
                 ["Higher timeframe trend is up", f"Trend strength rising (ADX {rg.adx:.0f})",
                  "Wide stop, no fixed target: let the trend pay"], atr,
                 target_rr=8.0, trail_atr=3.0, breakeven_r=2.0, trail_start_r=2.0, allow_partial=False)


# ---------------------------------------------------------------- NOMAD (daily)
def btc_risk_on(ctx) -> tuple[bool | None, float, float]:
    """BTC's last completed daily close vs its 200-day simple average."""
    if ctx is None:
        return None, 0.0, 0.0
    b = ctx.ind("BTC-USD", 86400)
    if b is None or b.n < 200:
        return None, 0.0, 0.0
    sma200 = float(np.mean(b.c[-200:]))
    return bool(b.c[-1] > sma200), float(b.c[-1]), sma200


def atr_sma(ind: Ind, n: int = 20) -> float:
    h, l, c = ind.h, ind.l, ind.c
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return float(np.mean(tr[-n:]))


def trend_follow(sig: Ind, trend: Ind, rg: Regime, price: float, ctx=None) -> Result:
    """Donchian breakout on daily closes, only while BTC is above its 200-day average."""
    s, c = sig.symbol, coin(sig.symbol)
    if sig.n < 61:
        return Watch("trend_follow", s, f"{c} needs 60 days of history first.", 0.0)
    on, btc_c, btc_sma = btc_risk_on(ctx)
    if on is None:
        return Watch("trend_follow", s, "Waiting for 200 days of BTC history to judge the market regime.", 0.0)
    if not on:
        gap = (btc_sma / btc_c - 1) * 100
        return Watch("trend_follow", s, f"Risk-off: BTC is {gap:.1f}% below its 200-day average ({fmt_price(btc_sma)}). "
                     "NOMAD stays in cash until the bull market returns.", 0.3)
    hi20 = float(np.max(sig.h[-21:-1]))
    sma50 = float(np.mean(sig.c[-50:]))
    close = sig.close
    mom = close / float(sig.c[-61]) - 1
    if not (close > hi20 and close > sma50):
        gap = (hi20 / close - 1) * 100
        return Watch("trend_follow", s, f"{c} closed {gap:.1f}% under its 20-day high ({fmt_price(hi20)}). "
                     "A daily close above it would start a trend trade.", max(0.05, 0.6 - gap / 10))
    a = atr_sma(sig)
    return Setup("trend_follow", s, close, close - 2 * a, None, 60 + min(max(mom * 100, 0), 35),
                 f"{c} closed at a new 20-day high in a bull market",
                 [f"Daily close {fmt_price(close)} > 20-day high {fmt_price(hi20)}",
                  f"Above its 50-day average; 60-day momentum {mom * 100:+.0f}%",
                  "No profit target: a trailing stop 3 ATR under the best close lets the trend run"],
                 a, target_rr=50, close_stop=True, hard_stop=close - 3.5 * a, allow_partial=False, rank=mom)


def review_trend_follow(pos, sig: Ind, ctx) -> str | None:
    """Called once per new daily bar for an open NOMAD position. Returns an exit reason or None."""
    close = sig.close
    a = atr_sma(sig)
    pos.peak_close = max(pos.peak_close or pos.entry_price, close)
    chand = pos.peak_close - 3 * a
    if chand > pos.stop:
        pos.stop = chand
        pos.trailing = True
    lo10 = float(np.min(sig.l[-11:-1]))
    on, _, _ = btc_risk_on(ctx)
    if close < pos.stop:
        return "TRAILING_STOP" if pos.trailing else "STOP_LOSS"
    if close < lo10:
        return "CHANNEL_EXIT"
    if on is False:
        return "REGIME_EXIT"
    return None


# -------------------------------------------------------------------- ORACLE
def oracle(sig: Ind, trend: Ind, rg: Regime, price: float, ctx=None) -> Result:
    s, c = sig.symbol, coin(sig.symbol)
    o = getattr(ctx, "oracle", None)
    if o is None or not o.ready:
        msg = o.status_text if o is not None else "Model offline."
        return Watch("oracle", s, msg, 0.0)
    pr = o.prediction(s, int(sig.t[-1]))
    if pr is None:
        return Watch("oracle", s, f"No fresh forecast for {c} yet.", 0.0)
    pred, thr = pr["pred"], pr["thr"]
    if not pr["regime"]:
        return Watch("oracle", s, f"{c} forecast {pred:+.2f}R, but BTC is below its 200-day average. "
                     "In walk-forward tests ORACLE only had an edge in risk-on markets, so it stands aside.",
                     0.2 + 0.1 * max(min(pred, 1.5), -1))
    if pred < thr:
        return Watch("oracle", s, f"{c} 14-day forecast {pred:+.2f}R. Needs {thr:+.2f}R (its top-10% bar) to trade.",
                     _clamp(0.45 + (pred - thr), 0.15, 0.85))
    dvol = pr["dvol"]
    stop = price * (1 - 2 * dvol)
    target = price * (1 + 4 * dvol)
    return Setup("oracle", s, price, stop, target, _clamp(80 + 10 * (pred - thr), 80, 99),
                 f"ORACLE forecasts {pred:+.2f}R for {c} over the next 14 days",
                 pr.get("reasons", [])[:3] + [f"Forecast beats its trade bar ({thr:+.2f}R)"],
                 price * dvol, target_rr=2.0, breakeven_r=99, trail_start_r=99, allow_partial=False,
                 time_stop_bars=336, rank=pred)


REVIEWERS = {"trend_follow": review_trend_follow}


STRATEGIES = {
    "oracle": oracle,
    "trend_follow": trend_follow,
    "trend_ride": trend_ride,
    "trend_pullback": trend_pullback,
    "breakout": breakout,
    "mean_reversion": mean_reversion,
    "momentum": momentum,
}
