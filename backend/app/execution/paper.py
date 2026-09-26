"""Paper exchange: simulated execution against the LIVE order book top.

Realism modelled:
  * maker/taker fees from the volume-tiered schedule (tier moves with 30-day volume)
  * market orders fill at the ask/bid after 80-350 ms simulated latency, using the
    price at fill time (so fast markets slip), and walk deeper book levels when the
    order is bigger than the top-of-book size
  * resting limit orders only fill when the market trades THROUGH the price; at-touch
    trades fill only partially (queue position), producing real partial fills
  * post-only orders that would cross the spread are rejected, like on Coinbase
"""
from __future__ import annotations

import random

from ..market.hub import MarketHub, Quote
from .base import ExecutionVenue, Fill, Order, OrderUpdate
from .fees import FeeTracker


class PaperExchange(ExecutionVenue):
    mode = "paper"

    def __init__(self, hub: MarketHub, fees: FeeTracker, latency: tuple[float, float] = (0.08, 0.35),
                 seed: int | None = None):
        self.hub = hub
        self.fees = fees
        self.latency = latency
        self.rng = random.Random(seed)
        self.orders: dict[str, Order] = {}
        self._updates: list[OrderUpdate] = []
        hub.trade_listeners.append(self._on_trade)

    # ------------------------------------------------------------------ api
    def fee_rates(self, now: float) -> tuple[float, float]:
        t = self.fees.tier(now)
        return t.maker, t.taker

    def submit(self, order: Order, now: float) -> None:
        order.created_ts = now
        q = self.hub.quote(order.symbol)
        if order.type == "limit" and order.post_only:
            crosses = (order.side == "buy" and order.limit_price >= q.ask) or \
                      (order.side == "sell" and order.limit_price <= q.bid)
            if crosses:
                order.status = "rejected"
                order.reason = "post-only order would cross the spread"
                self._updates.append(OrderUpdate(order, "rejected", done=True))
                return
        order.status = "open"
        marketable = order.type == "limit" and (
            (order.side == "buy" and order.limit_price >= q.ask) or (order.side == "sell" and order.limit_price <= q.bid))
        if marketable:
            # a limit order that crosses the spread executes immediately as a taker, at the book price
            order.type = "market"
        if order.type == "market":
            lo, hi = self.latency
            order.fill_at = now + (self.rng.uniform(lo, hi) if hi > 0 else 0.0)
        self.orders[order.id] = order

    def cancel(self, order_id: str, now: float, reason: str = "") -> None:
        o = self.orders.pop(order_id, None)
        if o and o.status == "open":
            o.status = "cancelled"
            o.reason = reason
            self._updates.append(OrderUpdate(o, "cancelled", done=True))

    def step(self, now: float) -> list[OrderUpdate]:
        for o in list(self.orders.values()):
            if o.type == "market" and now >= o.fill_at:
                self._fill_market(o, now)
        out, self._updates = self._updates, []
        return out

    # ------------------------------------------------------------ internals
    def _fill_market(self, o: Order, now: float) -> None:
        q = self.hub.quote(o.symbol)
        price, levels = self._walk_book(o.side, o.remaining, q)
        _, taker = self.fee_rates(now)
        self._apply_fill(o, o.remaining, price, taker, "taker", now, levels)

    def _walk_book(self, side: str, qty: float, q: Quote) -> tuple[float, int]:
        if side == "buy":
            p0, size0, sign = q.ask, q.ask_size, 1
        else:
            p0, size0, sign = q.bid, q.bid_size, -1
        step = max((q.ask - q.bid) * 1.5, p0 * 0.00003)
        remaining, cost, level = qty, 0.0, 0
        size0 = max(size0, 1e-9)
        while remaining > 1e-12:
            px = p0 + sign * level * step
            avail = size0 * (1 + 0.6 * level) if level < 40 else remaining
            take = min(avail, remaining)
            cost += take * px
            remaining -= take
            level += 1
        return cost / qty, level

    def _on_trade(self, symbol: str, price: float, size: float, ts: float) -> None:
        for o in list(self.orders.values()):
            if o.symbol != symbol or o.type != "limit" or o.status != "open":
                continue
            lp = o.limit_price
            through = (o.side == "buy" and price < lp) or (o.side == "sell" and price > lp)
            touch = price == lp
            if not (through or touch):
                continue
            qty = o.remaining if through else min(o.remaining, max(size, 0.0) * 0.5)
            if qty <= 1e-12:
                continue
            maker, _ = self.fee_rates(ts)
            self._apply_fill(o, qty, lp, maker, "maker", ts, 1)

    def _apply_fill(self, o: Order, qty: float, price: float, rate: float, liq: str, now: float,
                    levels: int) -> None:
        notional = qty * price
        fee = notional * rate
        o.avg_price = (o.avg_price * o.filled_qty + price * qty) / (o.filled_qty + qty)
        o.filled_qty += qty
        o.fees += fee
        self.fees.record(now, notional)
        done = o.remaining <= max(o.qty * 1e-9, 1e-12)
        if done:
            o.status = "filled"
            self.orders.pop(o.id, None)
        self._updates.append(OrderUpdate(o, "fill", Fill(qty, price, fee, liq, now, levels), done=done))
