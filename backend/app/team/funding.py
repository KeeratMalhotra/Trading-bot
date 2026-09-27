"""Records Coinbase's own hourly funding rates for the US perpetual-style futures the team trades.

Coinbase doesn't publish a funding history, so the only way to have one is to record it as it
happens. The public product endpoint reports the most recent hourly settlement
(future_product_details.funding_rate, with funding_time = the settlement at the END of the hour).
Rates are stored by the hour they cover (hour START), the same convention the Deribit proxy uses,
so the paper account - and later backtests - charge Coinbase's real rate wherever it was recorded.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import httpx

from ..ml.altdata import AltData
from .book import PERP_ID, perp_product

log = logging.getLogger("team.funding")
URL = "https://api.coinbase.com/api/v3/brokerage/market/products/{}"
POLL_S = 300
AFTER_SETTLE_S = 75      # poll shortly after each hourly settlement


class CoinbaseFunding:
    def __init__(self, alt: AltData, symbols: list[str]):
        self.alt = alt
        self.symbols = [s for s in symbols if s in PERP_ID]
        self.status = "starting"
        self.error = ""
        self.last_ok = 0.0

    async def poll_once(self, client: httpx.AsyncClient) -> int:
        new = 0
        for s in self.symbols:
            r = await client.get(URL.format(perp_product(s)))
            r.raise_for_status()
            f = r.json().get("future_product_details") or {}
            rate, when = f.get("funding_rate"), f.get("funding_time")
            if rate in (None, "") or not when:
                continue
            interval = int(str(f.get("funding_interval") or "3600s").rstrip("s") or 3600)
            settle = datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
            hour = int(settle - interval) // 3600 * 3600
            new += self.alt.add_coinbase_funding(s, hour, float(rate))
            await asyncio.sleep(0.2)
        return new

    async def run(self, stop: asyncio.Event) -> None:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "quorum/1.0"}) as client:
            while not stop.is_set():
                try:
                    n = await self.poll_once(client)
                    self.status, self.error, self.last_ok = "live", "", time.time()
                    if n:
                        log.info("recorded %d Coinbase funding rate(s)", n)
                except Exception as e:  # noqa: BLE001
                    self.status, self.error = "error", str(e)[:120]
                    log.warning("Coinbase funding poll failed: %s", e)
                now = time.time()
                nxt = min((now // 3600 + 1) * 3600 + AFTER_SETTLE_S, now + POLL_S)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(20.0, nxt - now))
                except asyncio.TimeoutError:
                    pass

    def hours_recorded(self) -> int:
        a = self.alt.cb.get("BTC-USD")
        return int(len(a)) if a is not None else 0

    def to_json(self) -> dict:
        return {"status": self.status, "error": self.error, "last_ok": self.last_ok or None,
                "hours": {s: int(len(self.alt.cb.get(s, ()))) for s in self.symbols}}
