"""The Bot Battle arena: three bots, one market, one (simulated) Coinbase account."""
from __future__ import annotations

import logging
import time
from collections import deque

from .accounting.tax import STATE_RATES, TaxSettings
from .config import (DEFAULT_FEE_MODE, PROFILES, STARTING_BALANCE, SYMBOLS, TF_LABEL,
                     TRADING_MODE)
from .engine.bot import Bot, HodlBot, money
from .engine.indicators import IndicatorCache
from .execution.base import ExecutionVenue
from .execution.fees import FeeTracker
from .execution.paper import PaperExchange
from .market.hub import MarketHub
from .storage import Store

log = logging.getLogger("arena")
EQUITY_SAMPLE_S = 30
PERSIST_S = 10


class Arena:
    def __init__(self, hub: MarketHub, store: Store | None, balance: float = STARTING_BALANCE,
                 venue: ExecutionVenue | None = None, latency: tuple[float, float] = (0.08, 0.35),
                 oracle=None, bot_ids: list[str] | None = None):
        self.hub = hub
        self.store = store
        self.balance = balance
        self.tax = TaxSettings()
        self.fees = FeeTracker(DEFAULT_FEE_MODE)
        self.venue = venue or PaperExchange(hub, self.fees, latency=latency)
        self.mode = self.venue.mode if venue else "paper"
        self.cache = IndicatorCache(hub)
        self.oracle = oracle
        self.bots = []
        for p in PROFILES:
            if bot_ids and p.id not in bot_ids:
                continue
            cls = HodlBot if p.kind == "hodl" else Bot
            b = cls(p, hub, self.venue, self.cache, lambda: self.tax, balance)
            if p.kind == "oracle":
                b.oracle = oracle
            self.bots.append(b)
        self.by_id = {b.id: b for b in self.bots}
        self.events: deque[dict] = deque(maxlen=400)
        self.equity: dict[str, deque[tuple[float, float]]] = {b.id: deque(maxlen=20000) for b in self.bots}
        self.outbox_events: list[dict] = []
        self.outbox_trades: list[dict] = []
        self.started = hub.now()
        self._last_equity = 0.0
        self._last_persist = 0.0
        self._last_tier = ""
        self.system_events: list[dict] = []

    # ============================================================ lifecycle
    def start(self, now: float) -> None:
        self.started = self.started or now
        for b in self.bots:
            b.next_scan = (now // 60 + 1) * 60 + b.p.scan_offset_s
            b._roll_day(now)
            b._update_status()
        self._last_tier = self.fees.tier(now).name

    def system(self, title: str, text: str, level: str = "info") -> None:
        self.system_events.append({"id": f"sys-{time.time_ns():x}", "ts": self.hub.now(), "bot": "system",
                                   "kind": "system", "title": title, "text": text, "symbol": None,
                                   "level": level, "data": {}})

    def step(self, now: float) -> None:
        self.hub.roll(now)
        for u in self.venue.step(now):
            bot = self.by_id.get(u.order.bot_id)
            if bot:
                bot.on_order_update(u, now)
        for b in self.bots:
            b.manage(now)
        for b in self.bots:
            if now >= b.next_scan:
                b.next_scan = (now // 60 + 1) * 60 + b.p.scan_offset_s
                b.scan(now)
        tier = self.fees.tier(now)
        if tier.name != self._last_tier:
            self.system("Fee tier changed", f"Coinbase fee tier is now {tier.name}: {tier.maker * 100:.2f}% maker / "
                        f"{tier.taker * 100:.2f}% taker (30-day volume {money(self.fees.volume_30d(now))}).", "good")
            self._last_tier = tier.name
        self._collect()
        if now - self._last_equity >= EQUITY_SAMPLE_S:
            self._last_equity = now
            rows = []
            for b in self.bots:
                eq = b.equity()
                self.equity[b.id].append((now, eq))
                rows.append((b.id, now, eq))
            if self.store:
                self.store.add_equity(rows)
        if self.store and now - self._last_persist >= PERSIST_S:
            self._last_persist = now
            self.save()

    def _collect(self) -> None:
        evs = self.system_events
        self.system_events = []
        trades = []
        for b in self.bots:
            evs.extend(b.events)
            b.events = []
            trades.extend(b.new_trades)
            b.new_trades = []
        if evs:
            evs.sort(key=lambda e: e["ts"])
            self.events.extend(evs)
            self.outbox_events.extend(evs)
            if self.store:
                self.store.add_events(evs)
        if trades:
            self.outbox_trades.extend(trades)
            if self.store:
                self.store.add_trades(trades)

    def drain(self) -> tuple[list[dict], list[dict]]:
        e, t = self.outbox_events, self.outbox_trades
        self.outbox_events, self.outbox_trades = [], []
        return e, t

    # ========================================================== persistence
    def save(self) -> None:
        if not self.store:
            return
        self.store.kv_set("settings", {"tax": self.tax.to_json(), "fee_mode": self.fees.mode})
        self.store.kv_set("fees", self.fees.dump())
        self.store.kv_set("arena", {"started": self.started, "balance": self.balance})
        for b in self.bots:
            self.store.kv_set(f"bot:{b.id}", b.dump())

    def load(self, now: float) -> bool:
        if not self.store:
            return False
        s = self.store.kv_get("settings")
        if s:
            t = s.get("tax", {})
            self.tax = TaxSettings(t.get("filing_status", "single"), t.get("other_income", 75_000),
                                   t.get("state", "XX"), t.get("state_rate_override"))
            self.fees.mode = s.get("fee_mode", self.fees.mode)
        f = self.store.kv_get("fees")
        if f:
            self.fees.load(f)
        a = self.store.kv_get("arena")
        restored = False
        if a:
            self.started = a["started"]
            for b in self.bots:
                d = self.store.kv_get(f"bot:{b.id}")
                if d:
                    b.load(d, now)
                    restored = True
        for bot_id, rows in self.store.equity(now - 7 * 86400).items():
            if bot_id in self.equity:
                self.equity[bot_id].extend(rows)
        self.events.extend(self.store.events(300))
        return restored

    def reset(self, now: float) -> None:
        for b in self.bots:
            for pos in list(b.positions.values()):
                for oid in (pos.entry_order_id, pos.tp_order_id, pos.partial_order_id, pos.exit_order_id):
                    if oid:
                        self.venue.cancel(oid, now, "reset")
        self.venue.step(now)
        for b in self.bots:
            b.reset(self.balance)
        self.fees.fills.clear()
        self.fees._vol = 0.0
        for d in self.equity.values():
            d.clear()
        self.events.clear()
        if self.store:
            self.store.clear()
        self.started = now
        self.start(now)
        self.system("Battle reset", f"All three bots start fresh with {money(self.balance)} each. May the best bot win.",
                    "good")
        self.save()

    # ================================================================ views
    def meta(self, now: float) -> dict:
        return {
            "mode": self.mode, "trading_mode_env": TRADING_MODE, "started": self.started,
            "starting_balance": self.balance, "symbols": SYMBOLS,
            "market": {"source": self.hub.status.source, "connected": self.hub.status.connected,
                       "message": self.hub.status.message},
            "fees": self.fees.to_json(now), "tax": self.tax.to_json(),
            "states": [{"code": k, "name": v[0], "rate": v[1]} for k, v in STATE_RATES.items()],
            "timeframes": TF_LABEL,
        }

    def live(self, now: float) -> dict:
        bots = [b.summary_json(now) for b in self.bots]
        ranked = sorted(bots, key=lambda x: x["equity"], reverse=True)
        for i, b in enumerate(ranked):
            b["rank"] = i + 1
        return {
            "ts": now, "prices": self.hub.prices_json(), "bots": bots,
            "positions": [p for b in self.bots for p in b.positions_json()],
            "market": {"source": self.hub.status.source, "connected": self.hub.status.connected,
                       "message": self.hub.status.message},
            "fees": {k: v for k, v in self.fees.to_json(now).items() if k != "tiers"},
            "regimes": {b.id: b.regimes for b in self.bots},
            "oracle": self.oracle.to_json() if hasattr(self.oracle, "to_json") else None,
        }

    def equity_json(self, max_points: int = 1500) -> dict:
        out = {}
        for bid, rows in self.equity.items():
            rows = list(rows)
            step = max(1, len(rows) // max_points)
            out[bid] = [[int(t), round(e, 2)] for t, e in rows[::step]]
            if rows and (not out[bid] or out[bid][-1][0] != int(rows[-1][0])):
                out[bid].append([int(rows[-1][0]), round(rows[-1][1], 2)])
        return out

    def snapshot(self, now: float) -> dict:
        trades = self.store.trades(limit=200) if self.store else [t for b in self.bots for t in b.closed]
        trades = [{k: v for k, v in t.items() if k != "timeline"} for t in trades]
        return {"meta": self.meta(now), "live": self.live(now), "events": list(self.events)[-250:],
                "trades": trades, "equity": self.equity_json()}
