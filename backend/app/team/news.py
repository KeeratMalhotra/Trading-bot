"""News desk: live crypto headlines (RSS), scored and tagged by coin.

Used as a LIVE-ONLY risk overlay (there's no free historical headline archive to test it on):
  * a coin with a severe negative headline (hack, exploit, delisting, lawsuit, outage...)
    is blocked from NEW long exposure for 48 hours
  * two or more severe market-wide headlines within 6 hours put the desk in "headline risk"
    mode (no new longs anywhere for 12 hours)
Everything it does is logged, so viewers can see exactly when news changed a decision.
"""
from __future__ import annotations

import asyncio
import email.utils
import hashlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import deque

import httpx

log = logging.getLogger("team.news")

FEEDS = {
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
}
COINS = {
    "BTC-USD": ("bitcoin", "btc"), "ETH-USD": ("ethereum", "ether", "eth"), "SOL-USD": ("solana", "sol"),
    "XRP-USD": ("xrp", "ripple"), "DOGE-USD": ("dogecoin", "doge"), "ADA-USD": ("cardano", "ada"),
    "AVAX-USD": ("avalanche", "avax"), "LINK-USD": ("chainlink", "link"), "LTC-USD": ("litecoin", "ltc"),
    "SUI-USD": ("sui",),
}
SEVERE = ("hack", "hacked", "exploit", "drained", "stolen", "delist", "halts withdrawals", "insolven",
          "bankrupt", "sec sues", "sec charges", "lawsuit", "indicted", "outage", "halted", "rug pull",
          "depeg", "ponzi", "fraud", "arrested", "ban on", "bans crypto")
NEG = {"crash": 2, "plunge": 2, "plunges": 2, "tumble": 1.5, "slump": 1.5, "selloff": 1.5, "sell-off": 1.5,
       "liquidation": 1, "liquidations": 1, "outflows": 1, "bearish": 1, "fear": 1, "warning": 1, "probe": 1.5,
       "fine": 1, "fined": 1.5, "risk": 0.5, "drops": 1, "falls": 1, "decline": 1, "dump": 1.5, "fud": 1}
POS = {"surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 1.5, "rallies": 1.5, "record high": 2,
       "all-time high": 2, "inflows": 1.5, "approval": 1.5, "approved": 1.5, "bullish": 1, "adoption": 1,
       "partnership": 1, "upgrade": 1, "launch": 0.5, "gains": 1, "jumps": 1.5, "climbs": 1, "etf": 0.5,
       "rate cut": 1.5, "breakout": 1}
MARKET_WORDS = ("crypto", "market", "exchange", "stablecoin", "sec", "fed", "etf", "regulat")


def _score(text: str) -> tuple[float, bool]:
    t = text.lower()
    severe = any(w in t for w in SEVERE)
    s = sum(v for w, v in POS.items() if w in t) - sum(v for w, v in NEG.items() if w in t)
    if severe:
        s -= 3
    return max(-1.0, min(1.0, s / 4)), severe


def _coins(text: str) -> list[str]:
    t = " " + re.sub(r"[^a-z0-9 ]", " ", text.lower()) + " "
    return [s for s, words in COINS.items() if any(f" {w} " in t for w in words)]


class NewsDesk:
    def __init__(self):
        self.items: deque[dict] = deque(maxlen=150)
        self.seen: set[str] = set()
        self.veto_until: dict[str, float] = {}
        self.risk_until = 0.0
        self.status = "starting"
        self.new_events: list[dict] = []

    def blocked(self, symbol: str, now: float) -> bool:
        return self.veto_until.get(symbol, 0) > now or self.risk_until > now

    def market_mood(self, now: float, hours: float = 24) -> float | None:
        xs = [i["score"] for i in self.items if now - i["ts"] < hours * 3600]
        return sum(xs) / len(xs) if xs else None

    def ingest(self, items: list[dict], now: float) -> None:
        fresh = []
        for it in sorted(items, key=lambda x: x["ts"]):
            key = hashlib.sha1((it["title"] + it["source"]).encode()).hexdigest()
            if key in self.seen:
                continue
            self.seen.add(key)
            score, severe = _score(it["title"] + " " + it.get("summary", ""))
            coins = _coins(it["title"])
            item = {**it, "id": key[:12], "score": round(score, 2), "severe": severe, "coins": coins}
            fresh.append(item)
            self.items.appendleft(item)
            if not severe or now - it["ts"] > 12 * 3600:
                continue
            if coins:
                for c in coins:
                    self.veto_until[c] = max(self.veto_until.get(c, 0), it["ts"] + 48 * 3600)
                self.new_events.append({"kind": "news_veto", "item": item})
            elif any(w in it["title"].lower() for w in MARKET_WORDS):
                recent = [i for i in self.items if i["severe"] and not i["coins"] and it["ts"] - i["ts"] < 6 * 3600]
                if len(recent) >= 2 and self.risk_until < now:
                    self.risk_until = now + 12 * 3600
                    self.new_events.append({"kind": "news_risk", "item": item})
        if fresh:
            self.new_events.append({"kind": "news", "items": fresh[-5:]})

    async def fetch(self, client: httpx.AsyncClient) -> list[dict]:
        out = []
        for src, url in FEEDS.items():
            try:
                r = await client.get(url, follow_redirects=True)
                root = ET.fromstring(r.content)
                for it in root.iter("item"):
                    title = (it.findtext("title") or "").strip()
                    link = (it.findtext("link") or "").strip()
                    pub = it.findtext("pubDate")
                    try:
                        ts = email.utils.parsedate_to_datetime(pub).timestamp() if pub else time.time()
                    except Exception:  # noqa: BLE001
                        ts = time.time()
                    desc = re.sub("<[^>]+>", " ", it.findtext("description") or "")[:300]
                    if title:
                        out.append({"title": title, "link": link, "ts": ts, "source": src, "summary": desc})
            except Exception as e:  # noqa: BLE001
                log.debug("feed %s failed: %s", src, e)
        return out

    async def run(self, stop: asyncio.Event) -> None:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0 (quorum news desk)"}) as client:
            while not stop.is_set():
                try:
                    items = await self.fetch(client)
                    self.ingest([i for i in items if time.time() - i["ts"] < 3 * 86400], time.time())
                    self.status = "live"
                except Exception as e:  # noqa: BLE001
                    log.warning("news cycle failed: %s", e)
                    self.status = "error"
                try:
                    await asyncio.wait_for(stop.wait(), timeout=300)
                except asyncio.TimeoutError:
                    pass

    def to_json(self, now: float, n: int = 30) -> dict:
        return {"status": self.status, "mood_24h": self.market_mood(now),
                "vetoes": {s: t for s, t in self.veto_until.items() if t > now},
                "headline_risk_until": self.risk_until if self.risk_until > now else None,
                "items": list(self.items)[:n]}
