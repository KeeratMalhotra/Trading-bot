"""Cash + FIFO tax lots per account (per-account basis tracking, as required since 2025)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

YEAR_SECONDS = 365 * 86400


@dataclass
class Lot:
    symbol: str
    qty: float
    unit_cost: float      # USD per coin, INCLUDING the buy fee (fees are part of cost basis)
    acquired_ts: float


@dataclass
class Disposal:
    """One line of a Form 8949 / 1099-DA style report."""
    symbol: str
    qty: float
    acquired_ts: float
    sold_ts: float
    proceeds: float       # net of the sell fee
    basis: float
    gain: float
    term: str             # short | long
    trade_id: str
    bot_id: str


class Portfolio:
    def __init__(self, bot_id: str, cash: float):
        self.bot_id = bot_id
        self.cash = cash
        self.reserved = 0.0   # cash held by resting buy orders
        self.lots: dict[str, list[Lot]] = {}
        self.disposals: list[Disposal] = []

    @property
    def available(self) -> float:
        return self.cash - self.reserved

    def qty(self, symbol: str) -> float:
        return sum(l.qty for l in self.lots.get(symbol, []))

    def basis(self, symbol: str) -> float:
        return sum(l.qty * l.unit_cost for l in self.lots.get(symbol, []))

    def holdings_value(self, prices: dict[str, float]) -> float:
        return sum(l.qty * prices.get(s, 0.0) for s, lots in self.lots.items() for l in lots)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.holdings_value(prices)

    def buy(self, symbol: str, qty: float, price: float, fee: float, ts: float) -> None:
        self.cash -= qty * price + fee
        self.lots.setdefault(symbol, []).append(Lot(symbol, qty, price + fee / qty, ts))

    def sell(self, symbol: str, qty: float, price: float, fee: float, ts: float, trade_id: str) -> list[Disposal]:
        self.cash += qty * price - fee
        unit_proceeds = price - fee / qty
        out: list[Disposal] = []
        lots = self.lots.get(symbol, [])
        remaining = qty
        while remaining > 1e-12 and lots:
            lot = lots[0]
            take = min(lot.qty, remaining)
            basis = take * lot.unit_cost
            proceeds = take * unit_proceeds
            term = "long" if ts - lot.acquired_ts > YEAR_SECONDS else "short"
            out.append(Disposal(symbol, take, lot.acquired_ts, ts, proceeds, basis, proceeds - basis,
                                term, trade_id, self.bot_id))
            lot.qty -= take
            remaining -= take
            if lot.qty <= 1e-12:
                lots.pop(0)
        if not lots:
            self.lots.pop(symbol, None)
        self.disposals.extend(out)
        return out

    # ------------------------------------------------------------ persistence
    def dump(self) -> dict:
        return {"cash": self.cash,
                "lots": [asdict(l) for ls in self.lots.values() for l in ls],
                "disposals": [asdict(d) for d in self.disposals]}

    def load(self, d: dict) -> None:
        self.cash = d["cash"]
        self.reserved = 0.0
        self.lots = {}
        for x in d.get("lots", []):
            self.lots.setdefault(x["symbol"], []).append(Lot(**x))
        self.disposals = [Disposal(**x) for x in d.get("disposals", [])]
