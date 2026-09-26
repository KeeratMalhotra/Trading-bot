"""Coinbase public market data: REST candle history + websocket ticker stream.

Public endpoints only - no API keys needed for market data.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx
import websockets

from .candles import Candle
from .hub import MarketHub

log = logging.getLogger("market.coinbase")

REST = "https://api.exchange.coinbase.com"
WS_URL = "wss://ws-feed.exchange.coinbase.com"
HEADERS = {"User-Agent": "bot-battle/1.0", "Accept": "application/json"}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def fetch_candles(client: httpx.AsyncClient, symbol: str, tf: int,
                        start: float | None = None, end: float | None = None) -> list[Candle]:
    params: dict[str, str | int] = {"granularity": tf}
    if start is not None and end is not None:
        params["start"] = _iso(start)
        params["end"] = _iso(end)
    for attempt in range(5):
        r = await client.get(f"{REST}/products/{symbol}/candles", params=params)
        if r.status_code == 429:
            await asyncio.sleep(1.0 + attempt)
            continue
        r.raise_for_status()
        rows = r.json()
        # [time, low, high, open, close, volume]
        return [Candle(int(x[0]), float(x[3]), float(x[2]), float(x[1]), float(x[4]), float(x[5]))
                for x in rows]
    raise RuntimeError(f"rate limited fetching {symbol} {tf}")


async def fetch_range(client: httpx.AsyncClient, symbol: str, tf: int, start: float, end: float) -> list[Candle]:
    """Paginate (300 candles per request)."""
    out: dict[int, Candle] = {}
    step = tf * 300
    t = start
    while t < end:
        chunk_end = min(t + step, end)
        for c in await fetch_candles(client, symbol, tf, t, chunk_end):
            if start <= c.t < end:
                out[c.t] = c
        t = chunk_end
        await asyncio.sleep(0.12)
    return [out[k] for k in sorted(out)]


async def load_history(hub: MarketHub) -> None:
    now = time.time()
    async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
        for symbol in hub.symbols:
            for tf, series in hub.series[symbol].items():
                candles = await fetch_candles(client, symbol, tf)
                series.seed(candles, now)
                await asyncio.sleep(0.12)
            last = hub.series[symbol][60]
            ref = last.forming or (last.closed[-1] if last.closed else None)
            if ref:
                q = hub.quotes[symbol]
                q.price = q.bid = q.ask = ref.c
                q.ts = now
                day = hub.series[symbol][3600].recent(24)
                q.open_24h = day[0].o if day else ref.c
            log.info("history loaded for %s", symbol)


async def run_ticker(hub: MarketHub, stop: asyncio.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20,
                                          max_size=2**22, open_timeout=15) as ws:
                await ws.send(json.dumps({
                    "type": "subscribe", "product_ids": hub.symbols,
                    "channels": ["ticker", "heartbeat"],
                }))
                hub.status.source = "coinbase"
                hub.status.connected = True
                hub.status.message = "Live market data - Coinbase"
                backoff = 1.0
                log.info("coinbase websocket connected")
                async for raw in ws:
                    if stop.is_set():
                        break
                    msg = json.loads(raw)
                    if msg.get("type") != "ticker":
                        if msg.get("type") == "error":
                            log.warning("coinbase ws error: %s", msg)
                        continue
                    try:
                        ts = datetime.fromisoformat(msg["time"].replace("Z", "+00:00")).timestamp()
                    except Exception:  # noqa: BLE001
                        ts = time.time()
                    hub.on_trade(
                        msg["product_id"], float(msg["price"]), float(msg.get("last_size") or 0), ts,
                        bid=float(msg.get("best_bid") or 0), ask=float(msg.get("best_ask") or 0),
                        bid_size=float(msg.get("best_bid_size") or 0),
                        ask_size=float(msg.get("best_ask_size") or 0),
                        open_24h=float(msg.get("open_24h") or 0),
                        volume_24h=float(msg.get("volume_24h") or 0),
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("coinbase websocket dropped: %s", e)
        hub.status.connected = False
        hub.status.message = "Reconnecting to Coinbase..."
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)



async def resync_loop(hub: MarketHub, stop: asyncio.Event) -> None:
    """After every candle close, replace the ticker-built candle with Coinbase's official one.

    The ticker stream undercounts volume (it batches trades), while all history comes from
    REST candles. Re-syncing keeps volume-based signals consistent between history and live.
    """
    last_bucket: dict[int, int] = {}
    async with httpx.AsyncClient(headers=HEADERS, timeout=15) as client:
        while not stop.is_set():
            now = time.time()
            await asyncio.sleep((now // 60 + 1) * 60 + 8 - now)
            now = time.time()
            for tf in sorted({tf for s in hub.series.values() for tf in s}):
                b = int(now // tf)
                if last_bucket.get(tf) == b:
                    continue
                first = tf not in last_bucket
                last_bucket[tf] = b
                if first:
                    continue
                for sym in hub.symbols:
                    try:
                        cs = await fetch_candles(client, sym, tf, (b - 3) * tf, b * tf)
                        hub.series[sym][tf].patch([c for c in cs if c.t < b * tf])
                    except Exception as e:  # noqa: BLE001
                        log.debug("resync %s %s failed: %s", sym, tf, e)
                    await asyncio.sleep(0.12)
