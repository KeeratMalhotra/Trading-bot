"""Static configuration: markets, risk profiles, fee schedule, runtime env settings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- env
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DB_PATH = DATA_DIR / "botbattle.sqlite3"
STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # empty = controls open (fine on localhost)
# auto / coinbase: live Coinbase prices, retrying until Coinbase answers (never swaps in fake prices)
# sim: the offline price simulator (demos only)
MARKET_SOURCE = os.getenv("MARKET_SOURCE", "auto")
# Yield earned on idle cash (cash not needed as futures margin), e.g. 0.0375 for USDC rewards.
# 0 = off. Only set it if your real account would earn it (e.g. Coinbase One + cash held as USDC).
CASH_APY = float(os.getenv("CASH_APY", "0"))
# Dashboard "what if" line: the same starting amount with this share held in BTC instead
# (bought once at the start). Display only, it never affects trading. 0 = hide it.
MIX_BTC_SHARE = min(max(float(os.getenv("MIX_BTC_SHARE", "0.4")), 0.0), 1.0)
MIX_REBALANCE = os.getenv("MIX_REBALANCE", "yearly")   # yearly (back to the split every Jan 1) | never
FEED_STALE_S = 60.0          # no Coinbase ticks for this long -> trading pauses until the feed is back
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "10000"))
TEAM_BALANCE = float(os.getenv("TEAM_BALANCE", "30000"))  # QUORUM team account
# US tax profile used for the estimates on the dashboard
TAX_FILING_STATUS = os.getenv("TAX_FILING_STATUS", "single")   # single | mfj
TAX_OTHER_INCOME = float(os.getenv("TAX_OTHER_INCOME", "75000"))
TAX_STATE = os.getenv("TAX_STATE", "XX")                       # two-letter code, XX = federal only
# Trading mode. Only "paper" is wired up. "live" additionally requires
# LIVE_TRADING_ACK=I_UNDERSTAND_REAL_MONEY_IS_AT_RISK and Coinbase CDP keys.
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

# ----------------------------------------------------------------------- markets
SYMBOLS: list[str] = [
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD",
    "ADA-USD", "AVAX-USD", "LINK-USD", "LTC-USD", "SUI-USD",
]
TIMEFRAMES: list[int] = [60, 300, 900, 3600, 21600, 86400]
TF_LABEL = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 21600: "6h", 86400: "1d"}

# Rough fallback top-of-book depth in USD when the feed does not provide sizes
# (backtests / simulator). Live mode uses real best bid/ask sizes from Coinbase.
DEFAULT_TOP_DEPTH_USD = {"BTC-USD": 60_000, "ETH-USD": 40_000}
DEFAULT_TOP_DEPTH_USD_OTHER = 8_000
DEFAULT_SPREAD = {"BTC-USD": 0.00002, "ETH-USD": 0.00005}
DEFAULT_SPREAD_OTHER = 0.0004


# ------------------------------------------------------------------ fee schedule
@dataclass(frozen=True)
class FeeTier:
    name: str
    min_volume: float  # trailing 30-day USD volume
    maker: float
    taker: float


# Coinbase Advanced Trade spot fee schedule (volume-tiered, maker/taker).
# Coinbase revises this schedule from time to time - verify the current numbers at
# https://www.coinbase.com/advanced-fees and edit here if they changed.
COINBASE_FEE_TIERS: list[FeeTier] = [
    FeeTier("Intro 1", 0, 0.0060, 0.0120),
    FeeTier("Intro 2", 10_000, 0.0035, 0.0075),
    FeeTier("Advanced 1", 50_000, 0.0025, 0.0040),
    FeeTier("Advanced 2", 100_000, 0.0015, 0.0025),
    FeeTier("Advanced 3", 1_000_000, 0.0010, 0.0020),
    FeeTier("VIP 1", 15_000_000, 0.0008, 0.0018),
    FeeTier("VIP 2", 75_000_000, 0.0005, 0.0016),
    FeeTier("VIP 3", 250_000_000, 0.0003, 0.0012),
    FeeTier("VIP 4", 400_000_000, 0.0000, 0.0005),
]
# "auto" = tier derived from the arena's combined simulated 30-day volume
# (all bots = portfolios inside one Coinbase account, like real Coinbase portfolios).
DEFAULT_FEE_MODE = os.getenv("FEE_MODE", "Advanced 2")


# ---------------------------------------------------------------- risk profiles
@dataclass(frozen=True)
class RiskProfile:
    id: str
    name: str
    label: str
    color: str
    tagline: str
    risk_per_trade: float       # fraction of equity risked (stop distance incl. costs)
    max_open: int               # concurrent positions
    max_position_pct: float     # max notional per position, fraction of equity
    max_exposure_pct: float     # max total notional in positions
    daily_loss_limit: float     # circuit breaker, fraction of day-start equity
    symbols: tuple[str, ...]
    signal_tf: int
    trend_tf: int
    strategies: tuple[str, ...]
    min_confidence: float
    min_net_rr: float           # reward/risk AFTER fees + slippage
    target_rr: float            # default take-profit in R
    entry_order: str            # "limit" (maker) | "market" (taker)
    limit_timeout_s: int
    breakeven_r: float
    trail_start_r: float
    trail_atr: float
    partial_r: float | None
    partial_pct: float
    max_hold_bars: int          # time stop, in signal-tf bars
    cooldown_bars: int          # after closing a trade on a symbol
    scan_offset_s: int          # staggers the thought feed between bots
    kind: str = "rules"         # rules | oracle | hodl
    generation: str = "Gen 1"
    benchmark: bool = False


PROFILES: list[RiskProfile] = [
    RiskProfile(
        id="oracle", name="ORACLE", label="Machine Learning", color="#a78bfa",
        tagline="Gradient-boosted model trained on 6 years of hourly data. Predicts each trade's outcome.",
        risk_per_trade=0.01, max_open=5, max_position_pct=0.25, max_exposure_pct=1.00,
        # slow systems: the breaker only guards against a catastrophic day
        daily_loss_limit=0.15, symbols=tuple(SYMBOLS),
        signal_tf=3600, trend_tf=3600, strategies=("oracle",),
        min_confidence=0, min_net_rr=1.5, target_rr=2.0,
        # market entries: in the 4-year engine replay, passive limit orders were adversely
        # selected (filled on the losers, missed the winners): +4% vs +34.5%
        entry_order="market", limit_timeout_s=0,
        breakeven_r=99, trail_start_r=99, trail_atr=3.0, partial_r=None, partial_pct=0.0,
        max_hold_bars=336, cooldown_bars=1, scan_offset_s=15,
        kind="oracle", generation="Gen 2",
    ),
    RiskProfile(
        id="nomad", name="NOMAD", label="Trend Follower", color="#38bdf8",
        tagline="Daily-chart trend follower. Few trades, rides big moves for weeks, hides in cash in bear markets.",
        risk_per_trade=0.01, max_open=8, max_position_pct=0.25, max_exposure_pct=1.00,
        daily_loss_limit=0.15, symbols=tuple(SYMBOLS),
        signal_tf=86400, trend_tf=86400, strategies=("trend_follow",),
        min_confidence=0, min_net_rr=0.0, target_rr=50.0,
        entry_order="limit", limit_timeout_s=3 * 3600,
        breakeven_r=99, trail_start_r=99, trail_atr=3.0, partial_r=None, partial_pct=0.0,
        max_hold_bars=100_000, cooldown_bars=1, scan_offset_s=35,
        generation="Gen 2",
    ),
    RiskProfile(
        id="low", name="SENTINEL", label="Low Risk", color="#34d399",
        tagline="Capital preservation. Trades only A+ setups on BTC & ETH.",
        risk_per_trade=0.005, max_open=2, max_position_pct=0.40, max_exposure_pct=0.70,
        daily_loss_limit=0.02, symbols=("BTC-USD", "ETH-USD"),
        signal_tf=3600, trend_tf=21600,
        strategies=("trend_pullback", "breakout", "mean_reversion"),
        min_confidence=68, min_net_rr=1.5, target_rr=3.0,
        entry_order="limit", limit_timeout_s=300,
        breakeven_r=1.0, trail_start_r=1.5, trail_atr=2.5, partial_r=1.5, partial_pct=0.5,
        max_hold_bars=48, cooldown_bars=2, scan_offset_s=5,
    ),
    RiskProfile(
        id="medium", name="TACTICIAN", label="Medium Risk", color="#fbbf24",
        tagline="Balanced. Rotates between trend, breakout and reversion on majors.",
        risk_per_trade=0.01, max_open=4, max_position_pct=0.40, max_exposure_pct=0.90,
        daily_loss_limit=0.04,
        symbols=("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "LINK-USD", "LTC-USD"),
        signal_tf=900, trend_tf=3600,
        strategies=("trend_pullback", "breakout", "mean_reversion"),
        min_confidence=62, min_net_rr=1.3, target_rr=2.5,
        entry_order="limit", limit_timeout_s=150,
        breakeven_r=1.0, trail_start_r=1.5, trail_atr=2.0, partial_r=1.5, partial_pct=0.33,
        max_hold_bars=64, cooldown_bars=2, scan_offset_s=25,
    ),
    RiskProfile(
        id="high", name="BERSERKER", label="High Risk", color="#f43f5e",
        tagline="Aggressive. Big size, market orders, all 10 coins including volatile alts.",
        risk_per_trade=0.02, max_open=6, max_position_pct=0.50, max_exposure_pct=1.00,
        daily_loss_limit=0.08, symbols=tuple(SYMBOLS),
        signal_tf=900, trend_tf=3600,
        strategies=("breakout", "momentum", "trend_pullback", "mean_reversion"),
        min_confidence=60, min_net_rr=1.3, target_rr=2.0,
        entry_order="market", limit_timeout_s=0,
        breakeven_r=0.8, trail_start_r=1.2, trail_atr=1.5, partial_r=None, partial_pct=0.0,
        max_hold_bars=48, cooldown_bars=1, scan_offset_s=45,
    ),
    RiskProfile(
        id="hodl", name="HODL", label="Benchmark", color="#94a3b8",
        tagline="Buys Bitcoin once and never sells. The bar every bot has to beat.",
        risk_per_trade=0, max_open=1, max_position_pct=1.0, max_exposure_pct=1.0,
        daily_loss_limit=1.0, symbols=("BTC-USD",), signal_tf=3600, trend_tf=3600, strategies=(),
        min_confidence=0, min_net_rr=0, target_rr=0, entry_order="market", limit_timeout_s=0,
        breakeven_r=99, trail_start_r=99, trail_atr=0, partial_r=None, partial_pct=0.0,
        max_hold_bars=10**9, cooldown_bars=0, scan_offset_s=55,
        kind="hodl", benchmark=True,
    ),
]
PROFILE_BY_ID = {p.id: p for p in PROFILES}
