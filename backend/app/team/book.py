"""The team's account: USD cash, spot coins (FIFO tax lots) and US perpetual-style futures.

Perps are Coinbase Derivatives nano contracts (fixed contract sizes, hourly funding,
Section 1256 tax treatment: 60% long-term / 40% short-term, marked to market at year end).
Spot and futures share one USD balance (Coinbase sweeps between them).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from ..accounting import tax as taxlib
from ..accounting.portfolio import Portfolio

CONTRACT = {"BTC-USD": 0.01, "ETH-USD": 0.1, "SOL-USD": 5, "XRP-USD": 500, "DOGE-USD": 5000,
            "ADA-USD": 1000, "AVAX-USD": 10, "LINK-USD": 50, "LTC-USD": 5, "SUI-USD": 500}
PERP_ID = {"BTC-USD": "BIP", "ETH-USD": "ETP", "SOL-USD": "SLP", "XRP-USD": "XPP", "DOGE-USD": "DOP",
           "ADA-USD": "ADP", "AVAX-USD": "AVP", "LINK-USD": "LNP", "LTC-USD": "LCP", "SUI-USD": "SUP"}
PERP_FEE = 0.0005            # per side; Coinbase quotes "as low as 0.02%" - we stay conservative
PERP_SLIP = {"BTC-USD": 0.0002, "ETH-USD": 0.0003}
PERP_SLIP_ALT = 0.0008       # thinner US perp books for alts
MARGIN = 0.20                # collateral reserved per $ of perp notional (5x, well inside 10x intraday)
STOP_SLIP = {"BTC-USD": 0.0010, "ETH-USD": 0.0015}   # extra slippage when a stop fires in a fast market
STOP_SLIP_ALT = 0.0030


PERP_EXPIRY = "20DEC30-CDE"  # current 5-year perpetual-style contracts (expire 2030-12-20)


def perp_name(symbol: str) -> str:
    return f"{symbol.split('-')[0]}-PERP"


def perp_product(symbol: str) -> str:
    """Coinbase product id, e.g. BTC-USD -> BIP-20DEC30-CDE."""
    return f"{PERP_ID[symbol]}-{PERP_EXPIRY}"


@dataclass
class PerpPos:
    qty: float = 0.0          # signed, in coins (multiple of the contract size)
    avg: float = 0.0
    funding: float = 0.0      # cumulative funding paid (+) / received (-)


@dataclass
class Fill:
    ts: float
    venue: str                # spot | perp
    symbol: str
    side: str                 # buy | sell
    qty: float
    price: float
    fee: float
    realized: float = 0.0
    agent_split: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["instrument"] = self.symbol if self.venue == "spot" else perp_name(self.symbol)
        d["contracts"] = round(abs(self.qty) / CONTRACT[self.symbol]) if self.venue == "perp" else None
        return d


class Book:
    def __init__(self, cash: float):
        self.cash = cash
        self.spot = Portfolio("team", 0.0)
        self.spot.cash = 0.0
        self.perps: dict[str, PerpPos] = {}
        self.fees = {"spot": 0.0, "perp": 0.0}
        self.funding_total = 0.0
        self.perp_realized: dict[int, float] = {}   # year -> realized P&L (incl. funding & fees)
        self.interest_total = 0.0                    # yield on idle cash (e.g. USDC rewards)
        self.interest_by_year: dict[int, float] = {}
        self.deposits = cash

    # ------------------------------------------------------------ valuation
    def spot_qty(self, s: str) -> float:
        return self.spot.qty(s)

    def spot_value(self, px: dict[str, float]) -> float:
        return sum(self.spot.qty(s) * px.get(s, 0.0) for s in self.spot.lots)

    def perp_unrealized(self, px: dict[str, float]) -> float:
        return sum(p.qty * (px.get(s, p.avg) - p.avg) for s, p in self.perps.items() if p.qty)

    def perp_gross(self, px: dict[str, float]) -> float:
        return sum(abs(p.qty) * px.get(s, p.avg) for s, p in self.perps.items())

    def equity(self, px: dict[str, float]) -> float:
        return self.cash + self.spot_value(px) + self.perp_unrealized(px)

    def exposure(self, px: dict[str, float]) -> dict:
        net: dict[str, float] = {}
        for s in self.spot.lots:
            net[s] = net.get(s, 0.0) + self.spot.qty(s) * px.get(s, 0.0)
        for s, p in self.perps.items():
            if p.qty:
                net[s] = net.get(s, 0.0) + p.qty * px.get(s, p.avg)
        eq = max(self.equity(px), 1e-9)
        long_ = sum(v for v in net.values() if v > 0)
        short = -sum(v for v in net.values() if v < 0)
        return {"net": (long_ - short) / eq, "gross": (long_ + short) / eq, "long": long_ / eq, "short": short / eq,
                "by_coin": {k: v / eq for k, v in net.items() if abs(v) > 1e-6}}

    # ------------------------------------------------------------ trading
    def trade_spot(self, s: str, qty: float, price: float, fee_rate: float, ts: float) -> Fill:
        fee = abs(qty) * price * fee_rate
        if qty > 0:
            self.spot.buy(s, qty, price, fee, ts)
            self.cash -= qty * price + fee
        else:
            q = min(-qty, self.spot.qty(s))
            self.spot.sell(s, q, price, fee, ts, "team")
            self.cash += q * price - fee
            qty = -q
        self.fees["spot"] += fee
        return Fill(ts, "spot", s, "buy" if qty > 0 else "sell", abs(qty), price, fee)

    def trade_perp(self, s: str, qty: float, price: float, ts: float) -> Fill:
        p = self.perps.setdefault(s, PerpPos())
        fee = abs(qty) * price * PERP_FEE
        realized = 0.0
        if p.qty == 0 or (p.qty > 0) == (qty > 0):          # open / add
            new = p.qty + qty
            p.avg = (p.avg * abs(p.qty) + price * abs(qty)) / abs(new)
            p.qty = new
        else:                                                 # reduce / close / flip
            close = min(abs(qty), abs(p.qty))
            realized = close * (price - p.avg) * (1 if p.qty > 0 else -1)
            rest = abs(qty) - close
            p.qty += qty
            if abs(p.qty) < 1e-12:
                p.qty, p.avg = 0.0, 0.0
            elif rest > 1e-12:
                p.avg = price
        self.cash += realized - fee
        y = time.gmtime(ts).tm_year
        self.perp_realized[y] = self.perp_realized.get(y, 0.0) + realized - fee
        self.fees["perp"] += fee
        if p.qty == 0:
            self.perps.pop(s, None)
        return Fill(ts, "perp", s, "buy" if qty > 0 else "sell", abs(qty), price, fee, realized)

    def accrue_funding(self, s: str, rate: float, price: float, ts: float) -> float:
        p = self.perps.get(s)
        if not p or not p.qty:
            return 0.0
        pay = p.qty * price * rate            # longs pay positive funding
        self.cash -= pay
        p.funding += pay
        self.funding_total += pay
        y = time.gmtime(ts).tm_year
        self.perp_realized[y] = self.perp_realized.get(y, 0.0) - pay
        return pay

    def idle_cash(self, px: dict[str, float]) -> float:
        """Cash not needed as futures margin."""
        return self.cash - MARGIN * self.perp_gross(px)

    def credit_interest(self, amount: float, ts: float) -> None:
        self.cash += amount
        self.interest_total += amount
        y = time.gmtime(ts).tm_year
        self.interest_by_year[y] = self.interest_by_year.get(y, 0.0) + amount

    # ------------------------------------------------------------ taxes
    def tax_estimate(self, settings: taxlib.TaxSettings, px: dict[str, float], year: int) -> dict:
        st = sum(d.gain for d in self.spot.disposals if d.term == "short" and time.gmtime(d.sold_ts).tm_year == year)
        lt = sum(d.gain for d in self.spot.disposals if d.term == "long" and time.gmtime(d.sold_ts).tm_year == year)
        s1256 = self.perp_realized.get(year, 0.0) + self.perp_unrealized(px)   # marked to market at year end
        interest = self.interest_by_year.get(year, 0.0)                        # ordinary income
        est = taxlib.estimate(settings, st + 0.4 * s1256, lt + 0.6 * s1256, ordinary=interest)
        est.update({"spot_short_term": st, "spot_long_term": lt, "section_1256": s1256, "interest": interest})
        return est

    # ------------------------------------------------------------ persistence
    def dump(self) -> dict:
        return {"cash": self.cash, "spot": self.spot.dump(), "fees": self.fees, "funding_total": self.funding_total,
                "perp_realized": self.perp_realized, "deposits": self.deposits,
                "interest_total": self.interest_total, "interest_by_year": self.interest_by_year,
                "perps": {s: asdict(p) for s, p in self.perps.items()}}

    def load(self, d: dict) -> None:
        self.cash = d["cash"]
        self.spot.load(d["spot"])
        self.spot.cash = 0.0
        self.fees = d.get("fees", self.fees)
        self.funding_total = d.get("funding_total", 0.0)
        self.perp_realized = {int(k): v for k, v in d.get("perp_realized", {}).items()}
        self.interest_total = d.get("interest_total", 0.0)
        self.interest_by_year = {int(k): v for k, v in d.get("interest_by_year", {}).items()}
        self.deposits = d.get("deposits", self.cash)
        self.perps = {s: PerpPos(**p) for s, p in d.get("perps", {}).items()}
