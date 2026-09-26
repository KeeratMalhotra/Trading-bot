"""MarketHub: the single source of truth for quotes and candles.

Feeds (Coinbase websocket, simulator, backtest replay) push trades into the hub.
Everything else (bots, paper exchange, UI) reads from it. All engine time comes
from the hub's market clock, which makes live trading and replays identical.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from ..config import (DEFAULT_SPREAD, DEFAULT_SPREAD_OTHER, DEFAULT_TOP_DEPTH_USD,
                      DEFAULT_TOP_DEPTH_USD_OTHER, TIMEFRAMES)
from .candles import CandleSeries


@dataclass(slots=True)
class Quote:
    symbol: str
    price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    ts: float = 0.0
    open_24h: float = 0.0
    volume_24h: float = 0.0

    def to_json(self) -> dict:
        chg = (self.price / self.open_24h - 1) if self.open_24h else 0.0
        return {"s": self.symbol, "p": self.price, "b": self.bid, "a": self.ask,
                "chg": chg, "ts": self.ts}


TradeListener = Callable[[str, float, float, float], None]  # symbol, price, size, ts


@dataclass
class FeedStatus:
    source: str = "starting"      # coinbase | sim | replay
    connected: bool = False
    message: str = "Connecting to market data..."
    last_msg: float = 0.0


class MarketHub:
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.quotes: dict[str, Quote] = {s: Quote(s) for s in symbols}
        self.series: dict[str, dict[int, CandleSeries]] = {
            s: {tf: CandleSeries(tf) for tf in TIMEFRAMES} for s in symbols
        }
        self.trade_listeners: list[TradeListener] = []
        self.status = FeedStatus()
        self.clock: float = 0.0  # market time (unix seconds)
        self.use_wall_clock = True

    # ------------------------------------------------------------------ clock
    def now(self) -> float:
        if self.use_wall_clock:
            return time.time()
        return self.clock

    # ------------------------------------------------------------------ input
    def on_trade(self, symbol: str, price: float, size: float, ts: float,
                 bid: float | None = None, ask: float | None = None,
                 bid_size: float | None = None, ask_size: float | None = None,
                 open_24h: float | None = None, volume_24h: float | None = None) -> None:
        q = self.quotes.get(symbol)
        if q is None or price <= 0:
            return
        q.price = price
        q.ts = ts
        if bid and ask and ask >= bid:
            q.bid, q.ask = bid, ask
        else:  # synthesize a realistic spread when the feed has no book
            half = price * DEFAULT_SPREAD.get(symbol, DEFAULT_SPREAD_OTHER) / 2
            q.bid, q.ask = price - half, price + half
        depth_usd = DEFAULT_TOP_DEPTH_USD.get(symbol, DEFAULT_TOP_DEPTH_USD_OTHER)
        q.bid_size = bid_size if bid_size else depth_usd / price
        q.ask_size = ask_size if ask_size else depth_usd / price
        if open_24h:
            q.open_24h = open_24h
        elif not q.open_24h:
            q.open_24h = price
        if volume_24h:
            q.volume_24h = volume_24h
        if ts > self.clock:
            self.clock = ts
        for series in self.series[symbol].values():
            series.update(price, size, ts)
        for fn in self.trade_listeners:
            fn(symbol, price, size, ts)
        self.status.last_msg = time.time()

    def roll(self, now: float) -> None:
        for per_sym in self.series.values():
            for series in per_sym.values():
                series.roll(now)

    # ------------------------------------------------------------------ access
    def quote(self, symbol: str) -> Quote:
        return self.quotes[symbol]

    def price(self, symbol: str) -> float:
        return self.quotes[symbol].price

    def ready(self, symbol: str) -> bool:
        return self.quotes[symbol].price > 0

    def prices_json(self) -> list[dict]:
        return [q.to_json() for q in self.quotes.values() if q.price > 0]
