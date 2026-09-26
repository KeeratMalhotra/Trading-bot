"""Execution venue interface shared by the paper exchange and (future) live exchange adapters.

Bots never talk to an exchange directly: they submit Orders and receive OrderUpdates
from `venue.step(now)`. Swapping PaperExchange for a live adapter changes nothing in
the strategy/risk code - which is what makes the demo behave like the real thing.
"""
from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

_ids = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}-{next(_ids)}"


@dataclass
class Order:
    bot_id: str
    symbol: str
    side: str                 # buy | sell
    type: str                 # market | limit
    qty: float
    purpose: str              # entry | take_profit | partial | exit
    position_id: str
    limit_price: float | None = None
    post_only: bool = False
    ref_price: float = 0.0    # price when the decision was made (slippage reference)
    id: str = field(default_factory=lambda: new_id("ord"))
    status: str = "new"       # new | open | filled | cancelled | rejected
    created_ts: float = 0.0
    fill_at: float = 0.0      # paper: simulated network/matching latency
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fees: float = 0.0
    reason: str = ""

    @property
    def remaining(self) -> float:
        return max(self.qty - self.filled_qty, 0.0)

    def to_json(self) -> dict:
        return {"id": self.id, "bot": self.bot_id, "symbol": self.symbol, "side": self.side,
                "type": self.type, "qty": self.qty, "limit": self.limit_price, "purpose": self.purpose,
                "status": self.status, "filled": self.filled_qty, "avg": self.avg_price,
                "created": self.created_ts}


@dataclass
class Fill:
    qty: float
    price: float
    fee: float
    liquidity: str   # maker | taker
    ts: float
    levels: int = 1  # book levels consumed (partial fills across the book)


@dataclass
class OrderUpdate:
    order: Order
    kind: str                 # fill | cancelled | rejected
    fill: Fill | None = None
    done: bool = False        # order reached a terminal state


class ExecutionVenue(ABC):
    mode: str = "paper"

    @abstractmethod
    def submit(self, order: Order, now: float) -> None: ...

    @abstractmethod
    def cancel(self, order_id: str, now: float, reason: str = "") -> None: ...

    @abstractmethod
    def step(self, now: float) -> list[OrderUpdate]: ...

    @abstractmethod
    def fee_rates(self, now: float) -> tuple[float, float]:
        """(maker, taker) fee rates currently applicable."""
