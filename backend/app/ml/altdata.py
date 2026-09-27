"""Alternative data: perpetual-futures funding rates and the Crypto Fear & Greed Index.

Funding history: Deribit public API (hourly, BTC/ETH since 2019; USDC-linear alts since ~2022).
It's used as a PROXY for Coinbase US perpetual-style futures funding, whose history isn't public.
Coins without their own history fall back to BTC's funding.

Fear & Greed: alternative.me daily index since 2018 (free, no key).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx
import numpy as np

from ..config import DATA_DIR

log = logging.getLogger("ml.altdata")
DERIBIT = "https://www.deribit.com/api/v2/public/get_funding_rate_history"
INSTRUMENTS = {
    "BTC-USD": "BTC-PERPETUAL", "ETH-USD": "ETH-PERPETUAL",
    "SOL-USD": "SOL_USDC-PERPETUAL", "XRP-USD": "XRP_USDC-PERPETUAL",
    "DOGE-USD": "DOGE_USDC-PERPETUAL", "ADA-USD": "ADA_USDC-PERPETUAL",
    "AVAX-USD": "AVAX_USDC-PERPETUAL", "LINK-USD": "LINK_USDC-PERPETUAL",
    "LTC-USD": "LTC_USDC-PERPETUAL",
}
START = 1577836800  # 2020-01-01


class AltData:
    def __init__(self, root: Path | None = None):
        self.root = root or (DATA_DIR / "altdata")
        self.root.mkdir(parents=True, exist_ok=True)
        self.funding: dict[str, np.ndarray] = {}  # symbol -> (n, 2) [hour_ts, hourly_rate]
        self.fng: np.ndarray = np.zeros((0, 2))   # (n, 2) [day_ts, value 0..100]

    # ------------------------------------------------------------ load/save
    def load(self) -> None:
        for s in INSTRUMENTS:
            p = self.root / f"funding-{s}.npy"
            self.funding[s] = np.load(p) if p.exists() else np.zeros((0, 2))
        p = self.root / "fear_greed.npy"
        self.fng = np.load(p) if p.exists() else np.zeros((0, 2))

    async def update(self) -> None:
        if not self.funding:
            self.load()
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "quorum/1.0"}) as client:
            for s, inst in INSTRUMENTS.items():
                try:
                    await self._update_funding(client, s, inst)
                except Exception as e:  # noqa: BLE001
                    log.warning("funding %s failed: %s", s, e)
            try:
                r = await client.get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"})
                rows = [(int(x["timestamp"]), float(x["value"])) for x in r.json()["data"]]
                self.fng = np.array(sorted(rows), dtype=float)
                np.save(self.root / "fear_greed.npy", self.fng)
            except Exception as e:  # noqa: BLE001
                log.warning("fear&greed failed: %s", e)

    async def _update_funding(self, client: httpx.AsyncClient, s: str, inst: str) -> None:
        arr = self.funding.get(s, np.zeros((0, 2)))
        start = int(arr[-1, 0]) + 3600 if len(arr) else START
        end = int(time.time() // 3600 * 3600)
        rows: dict[int, float] = {}
        t = start
        while t < end:
            b = min(t + 30 * 86400, end)
            r = await client.get(DERIBIT, params={"instrument_name": inst, "start_timestamp": t * 1000,
                                                  "end_timestamp": b * 1000})
            d = r.json()
            if "result" not in d:
                if t == start and not len(arr):
                    t = b
                    continue
                break
            for x in d["result"]:
                rows[int(x["timestamp"] // 1000 // 3600 * 3600)] = float(x["interest_1h"])
            t = b
            await asyncio.sleep(0.15)
        if rows:
            new = np.array(sorted(rows.items()), dtype=float)
            merged = np.vstack([arr, new]) if len(arr) else new
            _, idx = np.unique(merged[:, 0], return_index=True)
            self.funding[s] = merged[idx]
            np.save(self.root / f"funding-{s}.npy", self.funding[s])

    # ------------------------------------------------------------ access
    def hourly_funding(self, symbol: str, hours: np.ndarray) -> np.ndarray:
        """Hourly funding rate (fraction; positive = longs pay shorts) aligned to `hours`."""
        own = self.funding.get(symbol)
        btc = self.funding.get("BTC-USD")
        out = np.full(len(hours), np.nan)
        for src in (own, btc):
            if src is None or not len(src):
                continue
            idx = np.searchsorted(src[:, 0], hours, side="right") - 1
            ok = (idx >= 0) & np.isnan(out)
            ok &= np.abs(src[np.clip(idx, 0, None), 0] - hours) <= 6 * 3600
            out[ok] = src[idx[ok], 1]
        return np.nan_to_num(out, nan=0.0)

    def fear_greed_at(self, ts: float) -> float | None:
        if not len(self.fng):
            return None
        i = np.searchsorted(self.fng[:, 0], ts - 86400, side="right") - 1  # yesterday's (published) value
        return float(self.fng[i, 1]) if i >= 0 else None

    def summary(self) -> dict:
        out = {s: {"hours": int(len(a)), "from": time.strftime("%Y-%m-%d", time.gmtime(a[0, 0])),
                   "mean_annual_pct": round(float(a[:, 1].mean() * 24 * 365 * 100), 1)}
               for s, a in self.funding.items() if len(a)}
        if len(self.fng):
            out["fear_greed"] = {"days": int(len(self.fng)), "from": time.strftime("%Y-%m-%d", time.gmtime(self.fng[0, 0]))}
        return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    a = AltData()
    t0 = time.time()
    asyncio.run(a.update())
    print(f"{time.time() - t0:.0f}s", json.dumps(a.summary(), indent=1))
