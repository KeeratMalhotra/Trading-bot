"""OHLCV candle series built from live trades (with history seeding)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class Candle:
    t: int      # bucket start, unix seconds
    o: float
    h: float
    l: float
    c: float
    v: float

    def to_json(self) -> dict:
        return {"time": self.t, "open": self.o, "high": self.h, "low": self.l, "close": self.c, "volume": self.v}


class CandleSeries:
    def __init__(self, tf: int, maxlen: int = 600):
        self.tf = tf
        self.closed: deque[Candle] = deque(maxlen=maxlen)
        self.forming: Candle | None = None
        self.version = 0  # bumps every time a candle closes
        self._arr_cache: tuple[int, dict[str, np.ndarray]] | None = None

    # ------------------------------------------------------------------ seeding
    def seed(self, candles: list[Candle], now: float) -> None:
        candles = sorted(candles, key=lambda c: c.t)
        self.closed.clear()
        self.forming = None
        for c in candles:
            if c.t + self.tf <= now:
                self.closed.append(c)
            else:
                self.forming = c
        self.version += 1

    # ------------------------------------------------------------------ updates
    def update(self, price: float, size: float, ts: float) -> bool:
        """Apply a trade. Returns True when a candle closed."""
        b = int(ts // self.tf * self.tf)
        f = self.forming
        if f is None:
            self.forming = Candle(b, price, price, price, price, size)
            return False
        if b > f.t:
            self._close_until(b)
            self.forming = Candle(b, price, price, price, price, size)
            return True
        if b == f.t:
            if price > f.h:
                f.h = price
            if price < f.l:
                f.l = price
            f.c = price
            f.v += size
        return False

    def roll(self, now: float) -> bool:
        """Close the forming candle at bucket boundaries even without trades."""
        f = self.forming
        if f is None:
            return False
        b = int(now // self.tf * self.tf)
        if b > f.t:
            self._close_until(b)
            self.forming = Candle(b, f.c, f.c, f.c, f.c, 0.0)
            return True
        return False

    def _close_until(self, b: int) -> None:
        f = self.forming
        assert f is not None
        self.closed.append(f)
        t = f.t + self.tf
        gaps = 0
        while t < b and gaps < self.closed.maxlen:  # flat candles for silent periods
            self.closed.append(Candle(t, f.c, f.c, f.c, f.c, 0.0))
            t += self.tf
            gaps += 1
        self.version += 1

    # ------------------------------------------------------------------ access
    def arrays(self) -> dict[str, np.ndarray]:
        """Numpy arrays of CLOSED candles (cached per version)."""
        if self._arr_cache and self._arr_cache[0] == self.version:
            return self._arr_cache[1]
        cs = self.closed
        arr = {
            "t": np.fromiter((c.t for c in cs), dtype=np.int64, count=len(cs)),
            "o": np.fromiter((c.o for c in cs), dtype=float, count=len(cs)),
            "h": np.fromiter((c.h for c in cs), dtype=float, count=len(cs)),
            "l": np.fromiter((c.l for c in cs), dtype=float, count=len(cs)),
            "c": np.fromiter((c.c for c in cs), dtype=float, count=len(cs)),
            "v": np.fromiter((c.v for c in cs), dtype=float, count=len(cs)),
        }
        self._arr_cache = (self.version, arr)
        return arr

    def recent(self, n: int) -> list[Candle]:
        out = list(self.closed)[-n:]
        if self.forming:
            out.append(self.forming)
        return out
