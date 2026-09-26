"""Volume-tiered maker/taker fee schedule (Coinbase Advanced)."""
from __future__ import annotations

from collections import deque

from ..config import COINBASE_FEE_TIERS, FeeTier

WINDOW = 30 * 86400


class FeeTracker:
    def __init__(self, mode: str = "auto"):
        self.mode = mode  # "auto" or a tier name
        self.fills: deque[tuple[float, float]] = deque()
        self._vol = 0.0

    def record(self, ts: float, notional: float) -> None:
        self.fills.append((ts, notional))
        self._vol += notional

    def volume_30d(self, now: float) -> float:
        while self.fills and self.fills[0][0] < now - WINDOW:
            self._vol -= self.fills.popleft()[1]
        return max(self._vol, 0.0)

    def tier(self, now: float) -> FeeTier:
        if self.mode != "auto":
            for t in COINBASE_FEE_TIERS:
                if t.name == self.mode:
                    return t
        vol = self.volume_30d(now)
        current = COINBASE_FEE_TIERS[0]
        for t in COINBASE_FEE_TIERS:
            if vol >= t.min_volume:
                current = t
        return current

    def to_json(self, now: float) -> dict:
        t = self.tier(now)
        return {"mode": self.mode, "tier": t.name, "maker": t.maker, "taker": t.taker,
                "volume30d": self.volume_30d(now),
                "tiers": [{"name": x.name, "min": x.min_volume, "maker": x.maker, "taker": x.taker}
                          for x in COINBASE_FEE_TIERS]}

    def dump(self) -> dict:
        return {"mode": self.mode, "fills": list(self.fills)}

    def load(self, d: dict) -> None:
        self.mode = d.get("mode", self.mode)
        self.fills = deque(tuple(x) for x in d.get("fills", []))
        self._vol = sum(n for _, n in self.fills)
