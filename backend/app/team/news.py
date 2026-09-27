"""News desk: live crypto headlines (RSS), scored and tagged by coin.

Used as a LIVE-ONLY risk overlay (there's no free historical headline archive to test it on):
  * a coin with a severe negative headline (hack, exploit, delisting, outage...) is blocked
    from NEW long exposure for 48 hours
  * legal/crime headlines (lawsuit, fraud, arrested...) only block the coin for smaller coins:
    for BTC and ETH they're almost always about people who used them, not the network
  * headlines about a resolution ("lawsuit dismissed", "funds returned") are not severe
  * two or more severe market-wide headlines within 6 hours put the desk in "headline risk"
    mode (no new longs anywhere for 12 hours)
Matching is on whole words ("hackathon" is not a hack; "link" is not Chainlink).
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
def _rx(words) -> re.Pattern:
    """Whole-word, case-insensitive match of any of `words` (regex fragments; "$" allowed as a prefix)."""
    return re.compile(r"(?<![\w$])(?:" + "|".join(words) + r")(?!\w)", re.I)


COINS = {
    "BTC-USD": _rx(["bitcoin", "btc", r"\$btc"]),
    "ETH-USD": _rx(["ethereum", "ether", "eth", r"\$eth"]),
    "SOL-USD": _rx(["solana", "sol", r"\$sol"]),
    "XRP-USD": _rx(["xrp", r"ripple(?! effects?)"]),            # not "ripple effect"
    "DOGE-USD": _rx(["dogecoin", "doge"]),
    "ADA-USD": _rx(["cardano", "ada", r"\$ada"]),
    "AVAX-USD": _rx([r"avalanche(?! of)", "avax"]),            # not "an avalanche of liquidations"
    "LINK-USD": _rx(["chainlink", r"\$link"]),                  # not the everyday word "link"
    "LTC-USD": _rx(["litecoin", "ltc"]),
    "SUI-USD": _rx(["sui"]),
}
BIG = ("BTC-USD", "ETH-USD")
# something happened to the coin or the venue itself
EVENT = _rx([r"hack(?:s|ed|ers?)?", r"exploit(?:s|ed)?", "drained", "stolen", r"delist(?:s|ed|ing)?",
             r"halts? withdrawals", "halted", r"outages?", r"insolven(?:t|cy)", r"bankrupt(?:cy)?",
             r"rug ?pull(?:s|ed)?", r"depeg(?:s|ged)?", "ban on", r"bans crypto(?:currency)?"])
# legal / crime news
LEGAL = _rx(["sec sues", "sec charges", r"lawsuits?", "indicted", "arrested", "fraud", "ponzi"])
# the headline reports a resolution, not a new problem
RESOLVED = _rx([r"dismiss(?:es|ed)?", r"drops? (?:the |its )?(?:lawsuit|case|charges)", r"settle[sd]?",
                "acquitted", "cleared", r"overturn(?:s|ed)?", r"ends? (?:its )?(?:probe|investigation)",
                r"recover(?:s|ed)? (?:the )?(?:funds|stolen)", r"returns? (?:the )?(?:funds|stolen)"])
NEG = {"crash": 2, "plunge": 2, "plunges": 2, "tumble": 1.5, "slump": 1.5, "selloff": 1.5, "sell-off": 1.5,
       "liquidation": 1, "liquidations": 1, "outflows": 1, "bearish": 1, "fear": 1, "warning": 1, "probe": 1.5,
       "fine": 1, "fined": 1.5, "risk": 0.5, "drops": 1, "falls": 1, "decline": 1, "dump": 1.5, "fud": 1}
POS = {"surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 1.5, "rallies": 1.5, "record high": 2,
       "all-time high": 2, "inflows": 1.5, "approval": 1.5, "approved": 1.5, "bullish": 1, "adoption": 1,
       "partnership": 1, "upgrade": 1, "launch": 0.5, "gains": 1, "jumps": 1.5, "climbs": 1, "etf": 0.5,
       "rate cut": 1.5, "breakout": 1}
_NEG = [(_rx([re.escape(w)]), v) for w, v in NEG.items()]
_POS = [(_rx([re.escape(w)]), v) for w, v in POS.items()]
MARKET_WORDS = _rx([r"crypto\w*", r"markets?", r"exchanges?", r"stablecoins?", "sec", "fed", r"etfs?", r"regulat\w*"])
SEEN_MAX = 5000


def _classify(title: str, summary: str = "") -> tuple[float, str | None]:
    """Tone -1..1 (title + summary) and severity from the TITLE: "event", "legal" or None."""
    text = f"{title} {summary}"
    s = sum(v for rx, v in _POS if rx.search(text)) - sum(v for rx, v in _NEG if rx.search(text))
    kind = None
    if not RESOLVED.search(title):
        kind = "event" if EVENT.search(title) else ("legal" if LEGAL.search(title) else None)
    if kind:
        s -= 3
    return max(-1.0, min(1.0, s / 4)), kind


def _coins(text: str) -> list[str]:
    return [s for s, rx in COINS.items() if rx.search(text)]


def _veto_coins(coins: list[str], kind: str | None) -> list[str]:
    """Which of the coins a headline mentions it should block."""
    if kind == "event":
        return coins
    if kind == "legal":
        return [c for c in coins if c not in BIG]
    return []


class NewsDesk:
    def __init__(self):
        self.items: deque[dict] = deque(maxlen=150)
        self.seen: dict[str, None] = {}          # insertion-ordered, trimmed to SEEN_MAX
        self.veto_until: dict[str, float] = {}
        self.risk_until = 0.0
        self.status = "starting"
        self.new_events: list[dict] = []

    def blocked(self, symbol: str, now: float) -> bool:
        return self.veto_until.get(symbol, 0) > now or self.risk_until > now

    def blocked_until(self, symbol: str, now: float) -> float:
        u = max(self.veto_until.get(symbol, 0.0), self.risk_until)
        return u if u > now else 0.0

    def market_mood(self, now: float, hours: float = 24) -> float | None:
        xs = [i["score"] for i in self.items if now - i["ts"] < hours * 3600]
        return sum(xs) / len(xs) if xs else None

    def ingest(self, items: list[dict], now: float) -> None:
        fresh = []
        for it in sorted(items, key=lambda x: x["ts"]):
            key = hashlib.sha1((it["title"] + it["source"]).encode()).hexdigest()
            if key in self.seen:
                continue
            self.seen[key] = None
            if len(self.seen) > SEEN_MAX:
                for k in list(self.seen)[:SEEN_MAX // 5]:
                    del self.seen[k]
            score, kind = _classify(it["title"], it.get("summary", ""))
            coins = _coins(it["title"])
            item = {**it, "id": key[:12], "score": round(score, 2), "severe": kind is not None, "coins": coins}
            fresh.append(item)
            self.items.appendleft(item)
            if kind is None or now - it["ts"] > 12 * 3600:
                continue
            blocked = _veto_coins(coins, kind)
            if blocked:
                for c in blocked:
                    self.veto_until[c] = max(self.veto_until.get(c, 0), it["ts"] + 48 * 3600)
                self.new_events.append({"kind": "news_veto", "item": {**item, "coins": blocked}})
            elif not coins and MARKET_WORDS.search(it["title"]):
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
