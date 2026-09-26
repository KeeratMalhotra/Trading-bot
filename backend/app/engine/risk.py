"""Risk manager: decides IF a setup is worth trading and HOW BIG.

The key rule: a trade must still be worth it AFTER fees and slippage. Most retail
bots die from fees, so costs are baked into both reward/risk and position size.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import RiskProfile
from ..market.hub import Quote
from .strategies import Setup, coin, fmt_price

EST_STOP_SLIPPAGE = 0.0005   # extra slippage assumed when a stop fires in a fast market
MIN_NOTIONAL = 10.0
MAX_COST_TO_RISK = 0.30      # fees+slippage may use at most 30% of the planned risk


@dataclass
class Decision:
    ok: bool
    reason: str = ""
    code: str = ""          # machine-readable rejection code
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    partial: float | None = None
    qty: float = 0.0
    notional: float = 0.0
    risk_usd: float = 0.0
    net_rr: float = 0.0
    gross_rr: float = 0.0
    cost_pct: float = 0.0
    throttle: float = 1.0


def evaluate(setup: Setup, p: RiskProfile, q: Quote, *, equity: float, available: float,
             exposure: float, open_count: int, maker: float, taker: float,
             throttle: float) -> Decision:
    c = coin(setup.symbol)
    if setup.confidence < p.min_confidence:
        return Decision(False, f"{c} {setup.headline.split(' ', 1)[-1]}, but confidence {setup.confidence:.0f}% is below my {p.min_confidence:.0f}% bar.", "confidence")
    if open_count >= p.max_open:
        return Decision(False, f"{c} setup found, but I already hold {open_count}/{p.max_open} positions.", "max_open")

    use_limit = p.entry_order == "limit"
    entry = q.bid if use_limit else q.ask
    entry_fee = maker if use_limit else taker
    stop = setup.stop
    r_unit = entry - stop
    if r_unit <= 0:
        return Decision(False, f"{c} already traded below the invalidation level. Setup is dead.", "invalid")
    if entry > setup.ref_price + 0.6 * (setup.ref_price - stop):
        return Decision(False, f"{c} already ran {((entry / setup.ref_price) - 1) * 100:.2f}% past the signal. Missed it, not chasing.", "chase")

    round_trip = entry * (entry_fee + taker + EST_STOP_SLIPPAGE)
    if round_trip > MAX_COST_TO_RISK * r_unit:
        return Decision(False, f"{c} stop is only {r_unit / entry * 100:.2f}% away, but a round trip costs "
                        f"{round_trip / entry * 100:.2f}% in fees. Fees would eat {round_trip / r_unit:.0%} of my risk. Pass.",
                        "fees")
    target = setup.target if setup.target else entry + (setup.target_rr or p.target_rr) * r_unit
    if target <= entry * (1 + entry_fee + maker):
        return Decision(False, f"{c} target {fmt_price(target)} is too close to cover fees.", "fees")

    # costs per coin
    reward_net = (target - entry) - entry * entry_fee - target * maker
    risk_net = (entry - stop) + entry * entry_fee + stop * (taker + EST_STOP_SLIPPAGE)
    net_rr = reward_net / risk_net
    gross_rr = (target - entry) / r_unit
    cost_pct = entry_fee + maker
    if net_rr < p.min_net_rr:
        return Decision(False,
                        f"{c} {setup.strategy.replace('_', ' ')} looks OK ({gross_rr:.1f}R gross) but after "
                        f"{cost_pct * 100:.2f}% fees it's only {net_rr:.2f}R net. I need {p.min_net_rr:.1f}R. Pass.",
                        "net_rr", net_rr=net_rr, gross_rr=gross_rr, cost_pct=cost_pct)

    risk_usd = equity * p.risk_per_trade * throttle
    qty = risk_usd / risk_net
    cap_notional = min(equity * p.max_position_pct,
                       equity * p.max_exposure_pct - exposure,
                       available / (1 + entry_fee) * 0.995)
    if qty * entry > cap_notional:
        qty = max(cap_notional, 0.0) / entry
    qty = float(f"{qty:.8f}")
    notional = qty * entry
    if notional < MIN_NOTIONAL:
        return Decision(False, f"{c} setup found, but there's no free capital for a meaningful position.", "capital")

    partial = None
    if p.partial_r and not setup.target and setup.allow_partial:
        partial = entry + p.partial_r * r_unit
    return Decision(True, "", "", entry, stop, target, partial, qty, notional, qty * risk_net,
                    net_rr, gross_rr, cost_pct, throttle)
