"""The trading bot: scans markets, explains its thinking, opens and manages positions.

Every decision and every action is emitted as an event so it can be shown live.
"""
from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from ..accounting import tax as taxlib
from ..accounting.portfolio import Portfolio
from ..config import TF_LABEL, RiskProfile
from ..execution.base import ExecutionVenue, Order, OrderUpdate
from ..market.hub import MarketHub
from . import risk
from .indicators import IndicatorCache
from .regime import Regime, classify
from .strategies import STRATEGIES, STRATEGY_NAMES, Setup, Watch, coin, fmt_price

ET = ZoneInfo("America/New_York")
_evt_ids = itertools.count(1)

EXIT_LABELS = {
    "TAKE_PROFIT": "Take profit hit",
    "STOP_LOSS": "Stop loss hit",
    "TRAILING_STOP": "Trailing stop hit",
    "BREAKEVEN": "Breakeven stop hit",
    "TIME_STOP": "Time stop",
    "CIRCUIT_BREAKER": "Circuit breaker",
    "MANUAL": "Closed manually",
}


def day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")


def year_of(ts: float) -> int:
    return datetime.fromtimestamp(ts, ET).year


def money(x: float, sign: bool = False) -> str:
    s = f"${abs(x):,.2f}"
    if sign:
        return ("+" if x >= 0 else "−") + s
    return ("−" if x < 0 else "") + s


def qty_str(q: float) -> str:
    if q >= 1000:
        return f"{q:,.0f}"
    if q >= 1:
        return f"{q:,.3f}"
    return f"{q:.6f}"


@dataclass
class Position:
    id: str
    bot_id: str
    symbol: str
    strategy: str
    headline: str
    reasons: list[str]
    confidence: float
    opened_ts: float
    stop: float
    initial_stop: float
    target: float
    partial_price: float | None
    atr: float
    planned_qty: float
    planned_entry: float
    net_rr: float
    trail_atr: float = 0.0
    breakeven_r: float = 0.0
    trail_start_r: float = 0.0
    status: str = "opening"      # opening | open | closing
    qty: float = 0.0
    bought_qty: float = 0.0
    entry_price: float = 0.0
    entry_fees: float = 0.0
    exit_qty: float = 0.0
    exit_value: float = 0.0
    exit_fees: float = 0.0
    hwm: float = 0.0
    be_done: bool = False
    trailing: bool = False
    tightened: bool = False
    partial_done: bool = False
    exit_reason: str = ""
    entry_order_id: str = ""
    tp_order_id: str = ""
    partial_order_id: str = ""
    exit_order_id: str = ""
    last_stop_evt_ts: float = 0.0
    last_stop_evt_price: float = 0.0
    gain_st: float = 0.0
    gain_lt: float = 0.0
    slippage_bps: float = 0.0
    max_r: float = 0.0
    min_r: float = 0.0
    events: list[dict] = field(default_factory=list)  # per-trade timeline

    @property
    def r_unit(self) -> float:
        e = self.entry_price or self.planned_entry
        return max(e - self.initial_stop, e * 0.002, 1e-12)

    def r_at(self, price: float) -> float:
        e = self.entry_price or self.planned_entry
        return (price - e) / self.r_unit


class Bot:
    def __init__(self, profile: RiskProfile, hub: MarketHub, venue: ExecutionVenue, cache: IndicatorCache,
                 tax_settings: Callable[[], taxlib.TaxSettings], starting_balance: float):
        self.p = profile
        self.id = profile.id
        self.hub = hub
        self.venue = venue
        self.cache = cache
        self.tax_settings = tax_settings
        self.starting_balance = starting_balance
        self.reset(starting_balance)

    # ================================================================ state
    def reset(self, balance: float) -> None:
        self.portfolio = Portfolio(self.id, balance)
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, Order] = {}
        self.reserved_by_order: dict[str, float] = {}
        self.closed: deque[dict] = deque(maxlen=300)
        self.events: list[dict] = []
        self.new_trades: list[dict] = []
        self.stats = {"trades": 0, "wins": 0, "losses": 0, "gross_win": 0.0, "gross_loss": 0.0,
                      "fees": 0.0, "best": 0.0, "worst": 0.0, "peak": balance, "max_dd": 0.0,
                      "volume": 0.0, "slippage_usd": 0.0}
        self.day: dict = {}
        self.ytd = {"year": 0, "st": 0.0, "lt": 0.0}
        self.halted = False
        self.paused = False
        self.cooldown: dict[str, float] = {}
        self.benched: dict[str, float] = {}
        self.strategy_r: dict[str, list[float]] = {}
        self.seen_setups: dict[tuple, float] = {}
        self.consec_losses = 0
        self.scan_count = 0
        self.last_scan_symbol = ""
        self.recent_notes: dict[str, float] = {}
        self.next_scan = 0.0
        self.status_text = "Warming up"
        self.regimes: dict[str, str] = {}

    # ============================================================= helpers
    def prices(self) -> dict[str, float]:
        return {s: q.price for s, q in self.hub.quotes.items()}

    def equity(self) -> float:
        return self.portfolio.equity(self.prices())

    def exposure(self) -> float:
        px = self.prices()
        held = sum(p.qty * px.get(p.symbol, 0) for p in self.positions.values())
        return held + sum(self.reserved_by_order.values())

    def open_count(self) -> int:
        return len(self.positions)

    def position_for(self, symbol: str) -> Position | None:
        for p in self.positions.values():
            if p.symbol == symbol:
                return p
        return None

    def throttle(self) -> float:
        t = 1.0
        if self.consec_losses >= 5:
            t = 0.25
        elif self.consec_losses >= 3:
            t = 0.5
        if self.day and self.day["start_equity"] > 0:
            dd = 1 - self.equity() / self.day["start_equity"]
            if dd >= self.p.daily_loss_limit / 2:
                t *= 0.5
        return t

    def emit(self, kind: str, title: str, text: str = "", symbol: str | None = None,
             level: str = "info", data: dict | None = None, pos: Position | None = None) -> None:
        ev = {"id": f"{int(time.time() * 1000):x}-{next(_evt_ids)}", "ts": self.hub.now(), "bot": self.id, "kind": kind, "title": title,
              "text": text, "symbol": symbol, "level": level, "data": data or {}}
        self.events.append(ev)
        if pos is not None:
            pos.events.append({"ts": ev["ts"], "kind": kind, "title": title})
            del pos.events[:-30]

    def _submit(self, o: Order, now: float) -> None:
        self.orders[o.id] = o
        self.venue.submit(o, now)

    def _cancel(self, order_id: str, now: float, reason: str = "") -> None:
        if order_id and order_id in self.orders:
            self.venue.cancel(order_id, now, reason)

    def _release(self, order_id: str, amount: float | None = None) -> None:
        if order_id not in self.reserved_by_order:
            return
        if amount is None:
            self.reserved_by_order.pop(order_id)
        else:
            self.reserved_by_order[order_id] = max(0.0, self.reserved_by_order[order_id] - amount)
        self.portfolio.reserved = sum(self.reserved_by_order.values())

    def _atr(self, symbol: str) -> float | None:
        ind = self.cache.get(symbol, self.p.signal_tf)
        return ind.atr_now if ind else None

    # ============================================================ day / risk
    def _roll_day(self, now: float) -> None:
        k = day_key(now)
        y = year_of(now)
        if self.ytd["year"] != y:
            self.ytd = {"year": y, "st": 0.0, "lt": 0.0}
        if self.day.get("key") == k:
            return
        had_day = bool(self.day)
        eq = self.equity()
        self.day = {"key": k, "start_equity": eq, "realized": 0.0, "fees": 0.0, "trades": 0,
                    "wins": 0, "losses": 0, "ytd_st": self.ytd["st"], "ytd_lt": self.ytd["lt"]}
        if had_day:
            self.emit("system", "New trading day", f"Day-start balance {money(eq)}. Daily loss limit resets to "
                      f"−{self.p.daily_loss_limit * 100:.0f}%.", level="info")
        if self.halted:
            self.halted = False
            self.emit("risk", "Circuit breaker reset", "New day, clean slate. Back to scanning.", level="good")

    def _check_circuit(self, now: float) -> None:
        if self.halted or not self.day:
            return
        start = self.day["start_equity"]
        eq = self.equity()
        if start <= 0 or eq / start - 1 > -self.p.daily_loss_limit:
            return
        self.halted = True
        self.emit("risk", "CIRCUIT BREAKER TRIPPED",
                  f"Down {(1 - eq / start) * 100:.2f}% today, past my −{self.p.daily_loss_limit * 100:.0f}% daily limit. "
                  "Closing everything and standing down until midnight ET. Protecting the account comes first.",
                  level="bad")
        for pos in list(self.positions.values()):
            self._exit(pos, "CIRCUIT_BREAKER", now)

    # ================================================================ tick
    def manage(self, now: float) -> None:
        self._roll_day(now)
        for pos in list(self.positions.values()):
            self._manage_position(pos, now)
        eq = self.equity()
        if eq > self.stats["peak"]:
            self.stats["peak"] = eq
        dd = 1 - eq / self.stats["peak"] if self.stats["peak"] else 0
        self.stats["max_dd"] = max(self.stats["max_dd"], dd)
        self._check_circuit(now)

    def _manage_position(self, pos: Position, now: float) -> None:
        q = self.hub.quote(pos.symbol)
        price = q.price
        if pos.status == "opening":
            o = self.orders.get(pos.entry_order_id)
            if o and o.type == "limit":
                if price <= pos.stop:
                    self._cancel(o.id, now, f"{coin(pos.symbol)} fell to the stop level before filling. Setup invalidated.")
                elif now - o.created_ts >= self.p.limit_timeout_s:
                    self._cancel(o.id, now, f"Limit order not filled after {self.p.limit_timeout_s // 60} min. Letting it go.")
                elif price > pos.planned_entry + 0.5 * (pos.planned_entry - pos.initial_stop):
                    self._cancel(o.id, now, f"{coin(pos.symbol)} ran away without me. Not chasing.")
        if pos.qty <= 0 or pos.status == "closing":
            return
        pos.hwm = max(pos.hwm, price)
        r_now = pos.r_at(price)
        pos.max_r, pos.min_r = max(pos.max_r, r_now), min(pos.min_r, r_now)

        if price <= pos.stop:
            if pos.trailing and pos.stop > pos.entry_price:
                reason = "TRAILING_STOP"
            elif pos.be_done and pos.stop >= pos.entry_price:
                reason = "BREAKEVEN"
            else:
                reason = "STOP_LOSS"
            self._exit(pos, reason, now)
            return
        if pos.status != "open":
            return

        maker, taker = self.venue.fee_rates(now)
        if not pos.be_done and r_now >= (pos.breakeven_r or self.p.breakeven_r):
            pos.be_done = True
            fee_pct = pos.entry_fees / max(pos.entry_price * pos.bought_qty, 1e-12)
            be = pos.entry_price * (1 + fee_pct + taker + 0.0003)
            if pos.stop < be < price:
                pos.stop = be
                pos.last_stop_evt_ts, pos.last_stop_evt_price = now, be
                self.emit("stop", f"{coin(pos.symbol)} stop moved to breakeven",
                          f"Up {r_now:.1f}R, so the stop moves to {fmt_price(be)} (entry + fees). "
                          "This trade can no longer turn into a real loss.", pos.symbol, "good", pos=pos)

        trail_mult = pos.trail_atr or self.p.trail_atr
        if r_now >= (pos.trail_start_r or self.p.trail_start_r):
            atr = self._atr(pos.symbol) or pos.atr
            trail = pos.hwm - trail_mult * atr
            if trail > pos.stop:
                first = not pos.trailing
                pos.trailing = True
                pos.stop = trail
                locked = pos.qty * (trail - pos.entry_price) - pos.entry_fees * (pos.qty / pos.bought_qty) - pos.qty * trail * taker
                if first:
                    pos.last_stop_evt_ts, pos.last_stop_evt_price = now, trail
                    self.emit("stop", f"{coin(pos.symbol)} trailing stop ON",
                              f"Up {r_now:.1f}R. The stop now follows price {trail_mult:.1f} ATR behind "
                              f"({fmt_price(trail)}), locking in about {money(locked, True)}.", pos.symbol, "good", pos=pos)
                elif trail - pos.last_stop_evt_price >= 0.25 * pos.r_unit and now - pos.last_stop_evt_ts >= 30:
                    pos.last_stop_evt_ts, pos.last_stop_evt_price = now, trail
                    self.emit("stop", f"{coin(pos.symbol)} stop raised to {fmt_price(trail)}",
                              f"Locking in {money(locked, True)} while letting the winner run.", pos.symbol, "good", pos=pos)

    # ================================================================ scan
    def scan(self, now: float) -> None:
        self.scan_count += 1
        self._roll_day(now)
        if self.paused:
            self.status_text = "Paused"
            return
        if self.halted:
            self.status_text = "Halted: daily loss limit"
            if self.scan_count % 10 == 0:
                self.emit("thought", "Standing down", "Daily loss limit hit. No new trades until midnight ET.", level="warn")
            return
        maker, taker = self.venue.fee_rates(now)
        watches: list[Watch] = []
        regimes: dict[str, Regime] = {}
        for sym in self.p.symbols:
            if not self.hub.ready(sym):
                continue
            sig = self.cache.get(sym, self.p.signal_tf)
            tr = self.cache.get(sym, self.p.trend_tf)
            if sig is None or tr is None:
                continue
            rg = classify(sig, tr)
            regimes[sym] = rg
            self.regimes[sym] = rg.name
            held = self.position_for(sym)
            if held:
                self._review_position(held, rg, now)
                continue
            if self.cooldown.get(sym, 0) > now:
                continue
            price = self.hub.price(sym)
            best_setup: Setup | None = None
            best_watch: Watch | None = None
            for name in self.p.strategies:
                if self.benched.get(name, 0) > now:
                    continue
                res = STRATEGIES[name](sig, tr, rg, price)
                if isinstance(res, Setup):
                    if best_setup is None or res.confidence > best_setup.confidence:
                        best_setup = res
                elif best_watch is None or res.interest > best_watch.interest:
                    best_watch = res
            if best_setup:
                key = (sym, best_setup.strategy, int(sig.t[-1]))
                if key not in self.seen_setups:
                    self.seen_setups[key] = now
                    self._consider(best_setup, rg, now, maker, taker)
            elif best_watch:
                watches.append(best_watch)
        self._scan_thought(watches, regimes, now)
        self._update_status()
        if self.scan_count % 60 == 0:
            self.seen_setups = {k: v for k, v in self.seen_setups.items() if now - v < 86400}

    def _update_status(self) -> None:
        n = self.open_count()
        if n:
            self.status_text = f"In {n} trade{'s' if n > 1 else ''} · scanning {len(self.p.symbols)} markets"
        else:
            self.status_text = f"Scanning {len(self.p.symbols)} markets"

    def _scan_thought(self, watches: list[Watch], regimes: dict[str, Regime], now: float) -> None:
        # Occasionally narrate an open position instead of scanning results
        if self.positions and self.scan_count % 3 == 0:
            pos = list(self.positions.values())[(self.scan_count // 3) % len(self.positions)]
            if pos.status == "open" and pos.qty > 0:
                price = self.hub.price(pos.symbol)
                pnl = self._pos_pnl(pos, price)
                self.emit("thought", f"Holding {coin(pos.symbol)}",
                          f"{pnl['r']:+.2f}R ({money(pnl['net'], True)} after fees). Stop {fmt_price(pos.stop)}, "
                          f"target {fmt_price(pos.target)}. Letting the plan work.", pos.symbol)
                return
        if not regimes:
            return
        # forget notes older than 15 minutes so the feed never repeats itself
        self.recent_notes = {k: v for k, v in self.recent_notes.items() if now - v < 900}
        fresh = [w for w in watches if w.note not in self.recent_notes and w.interest >= 0.15]
        if fresh and self.scan_count % 4 != 0:
            best = max(fresh, key=lambda w: w.interest - (0.3 if w.symbol == self.last_scan_symbol else 0))
            self.last_scan_symbol = best.symbol
            self.recent_notes[best.note] = now
            self.emit("thought", f"Watching {coin(best.symbol)}", best.note, best.symbol,
                      data={"strategy": STRATEGY_NAMES[best.strategy]})
            return
        if self.scan_count % 4 != 0 and self.scan_count % 3 != 0:
            return  # nothing new to say: stay quiet rather than repeat
        counts = {"TRENDING_UP": 0, "TRENDING_DOWN": 0, "RANGING": 0, "VOLATILE": 0}
        for rg in regimes.values():
            counts[rg.name] += 1
        n = len(regimes)
        parts = [f"{v} {k.lower().replace('trending_', '').replace('_', ' ')}" for k, v in counts.items() if v]
        if counts["TRENDING_DOWN"] >= n / 2:
            mood = "Market is heavy. Spot bots can't short, so cash is my position."
        elif counts["TRENDING_UP"] >= n / 2:
            mood = "Plenty of strength out there. Hunting for clean entries."
        elif counts["VOLATILE"] >= n / 3:
            mood = "Choppy and violent. Staying picky."
        else:
            mood = "Mostly sideways. Patience pays in ranges."
        self.emit("thought", f"Scanned {n} markets ({TF_LABEL[self.p.signal_tf]})",
                  f"{', '.join(parts)}. {mood}")

    def _review_position(self, pos: Position, rg: Regime, now: float) -> None:
        if pos.status != "open" or pos.qty <= 0:
            return
        price = self.hub.price(pos.symbol)
        r_now = pos.r_at(price)
        bars = (now - pos.opened_ts) / self.p.signal_tf
        if bars >= self.p.max_hold_bars and r_now < 0.5:
            hours = (now - pos.opened_ts) / 3600
            self.emit("thought", f"{coin(pos.symbol)} is going nowhere",
                      f"{hours:.1f}h in and only {r_now:+.2f}R. Dead money. Freeing up the capital.", pos.symbol, "warn", pos=pos)
            self._exit(pos, "TIME_STOP", now)
            return
        if rg.trend == "down" and r_now > 0.3 and not pos.tightened:
            atr = self._atr(pos.symbol) or pos.atr
            new = price - 1.0 * atr
            if new > pos.stop:
                pos.stop = new
                pos.tightened = True
                self.emit("stop", f"{coin(pos.symbol)} stop tightened",
                          f"The bigger trend just turned down. Pulling the stop up to {fmt_price(new)} to protect the gain.",
                          pos.symbol, "warn", pos=pos)

    # ============================================================== entries
    def _consider(self, setup: Setup, rg: Regime, now: float, maker: float, taker: float) -> None:
        c = coin(setup.symbol)
        sname = STRATEGY_NAMES[setup.strategy]
        self.emit("setup", f"Setup: {c} {sname}", f"{setup.headline}. " + " · ".join(setup.reasons),
                  setup.symbol, "info", {"confidence": round(setup.confidence), "strategy": sname,
                                         "regime": rg.label})
        d = risk.evaluate(setup, self.p, self.hub.quote(setup.symbol), equity=self.equity(),
                          available=self.portfolio.available, exposure=self.exposure(),
                          open_count=self.open_count(), maker=maker, taker=taker, throttle=self.throttle())
        if not d.ok:
            self.emit("pass", f"Pass on {c}", d.reason, setup.symbol, "warn", {"code": d.code})
            return
        self._place_entry(setup, d, now, maker)

    def _place_entry(self, setup: Setup, d: risk.Decision, now: float, maker: float) -> None:
        c = coin(setup.symbol)
        pos = Position(
            id=f"{self.id}-{int(now)}-{setup.symbol}", bot_id=self.id, symbol=setup.symbol,
            strategy=setup.strategy, headline=setup.headline, reasons=setup.reasons,
            confidence=setup.confidence, opened_ts=now, stop=d.stop, initial_stop=d.stop, target=d.target,
            partial_price=d.partial, atr=setup.atr, planned_qty=d.qty, planned_entry=d.entry, net_rr=d.net_rr,
            trail_atr=setup.trail_atr or self.p.trail_atr, breakeven_r=setup.breakeven_r or self.p.breakeven_r,
            trail_start_r=setup.trail_start_r or self.p.trail_start_r,
        )
        limit = self.p.entry_order == "limit"
        o = Order(self.id, setup.symbol, "buy", "limit" if limit else "market", d.qty, "entry", pos.id,
                  limit_price=d.entry if limit else None, post_only=limit, ref_price=d.entry)
        self.positions[pos.id] = pos
        pos.entry_order_id = o.id
        if limit:
            self.reserved_by_order[o.id] = d.qty * d.entry * (1 + maker)
            self.portfolio.reserved = sum(self.reserved_by_order.values())
        throttle_note = f" Size cut to {d.throttle:.0%} after recent losses." if d.throttle < 1 else ""
        kind = "LIMIT (maker, post-only)" if limit else "MARKET (taker)"
        self.emit("order", f"BUY {qty_str(d.qty)} {c} · {kind}",
                  f"{'Limit ' + fmt_price(d.entry) if limit else '~' + fmt_price(d.entry)} · {money(d.notional)} position · "
                  f"risking {money(d.risk_usd)} ({d.risk_usd / max(self.equity(), 1) * 100:.2f}% of account) · "
                  f"{d.net_rr:.1f}R net after fees.{throttle_note}",
                  setup.symbol, "action", {"side": "buy", "type": o.type, "qty": d.qty, "price": d.entry}, pos=pos)
        self._submit(o, now)

    def _place_exit_orders(self, pos: Position, now: float) -> None:
        qty = pos.qty
        if pos.partial_price and not pos.partial_done and self.p.partial_pct > 0:
            pq = float(f"{qty * self.p.partial_pct:.8f}")
            if pq > 0 and (qty - pq) > 0:
                po = Order(self.id, pos.symbol, "sell", "limit", pq, "partial", pos.id, limit_price=pos.partial_price)
                pos.partial_order_id = po.id
                self._submit(po, now)
                qty = qty - pq
        to = Order(self.id, pos.symbol, "sell", "limit", float(f"{qty:.8f}"), "take_profit", pos.id,
                   limit_price=pos.target)
        pos.tp_order_id = to.id
        self._submit(to, now)

    def _activate(self, pos: Position, now: float) -> None:
        if pos.status != "opening":
            return
        pos.status = "open"
        pos.hwm = max(pos.hwm, pos.entry_price)
        self._place_exit_orders(pos, now)
        c = coin(pos.symbol)
        risk_usd = pos.bought_qty * (pos.entry_price - pos.initial_stop)
        stop_pct = (pos.initial_stop / pos.entry_price - 1) * 100
        tgt_pct = (pos.target / pos.entry_price - 1) * 100
        partial = f" · take 1/{round(1 / self.p.partial_pct)} at {fmt_price(pos.partial_price)}" \
            if pos.partial_price and self.p.partial_pct else ""
        self.emit("open", f"LONG {c} @ {fmt_price(pos.entry_price)}",
                  f"Stop {fmt_price(pos.initial_stop)} ({stop_pct:.2f}%) · Target {fmt_price(pos.target)} "
                  f"(+{tgt_pct:.2f}%){partial} · Max loss ~ {money(risk_usd)}. Stop & target orders are set.",
                  pos.symbol, "action",
                  {"entry": pos.entry_price, "stop": pos.initial_stop, "target": pos.target,
                   "qty": pos.bought_qty, "strategy": STRATEGY_NAMES[pos.strategy]}, pos=pos)

    # ================================================================ exits
    def _exit(self, pos: Position, reason: str, now: float) -> None:
        if pos.status == "closing":
            return
        for oid in (pos.entry_order_id, pos.tp_order_id, pos.partial_order_id):
            self._cancel(oid, now, "position exit")
        if pos.qty <= 1e-12:
            if pos.exit_qty > 0:
                self._finalize(pos, reason, now)
            else:
                self.positions.pop(pos.id, None)
            return
        pos.status = "closing"
        pos.exit_reason = reason
        q = self.hub.quote(pos.symbol)
        o = Order(self.id, pos.symbol, "sell", "market", pos.qty, "exit", pos.id, ref_price=q.bid)
        pos.exit_order_id = o.id
        lvl = "bad" if pos.r_at(q.price) < 0 else "action"
        self.emit("order", f"SELL {qty_str(pos.qty)} {coin(pos.symbol)} · MARKET",
                  f"{EXIT_LABELS.get(reason, reason)} at ~{fmt_price(q.bid)}. Executing now.", pos.symbol, lvl,
                  {"side": "sell", "type": "market", "qty": pos.qty, "reason": reason}, pos=pos)
        self._submit(o, now)

    def close_manual(self, pos_id: str, now: float) -> bool:
        pos = self.positions.get(pos_id)
        if not pos:
            return False
        self._exit(pos, "MANUAL", now)
        return True

    # ======================================================= order updates
    def on_order_update(self, u: OrderUpdate, now: float) -> None:
        o = u.order
        if u.done:
            self.orders.pop(o.id, None)
        pos = self.positions.get(o.position_id)
        if o.purpose == "entry":
            self._on_entry_update(u, pos, now)
            return
        if pos is None:
            return
        if u.kind == "fill" and u.fill:
            f = u.fill
            self._record_sell(pos, f.qty, f.price, f.fee, now)
            c = coin(pos.symbol)
            if o.purpose == "partial":
                pos.partial_done = True
                gain = f.qty * (f.price - pos.entry_price) - f.fee - pos.entry_fees * f.qty / pos.bought_qty
                self.emit("partial", f"{c} partial profit {money(gain, True)}",
                          f"Sold {qty_str(f.qty)} {c} at {fmt_price(f.price)} (maker fee {money(f.fee)}). "
                          "Banking some profit, the rest rides.", pos.symbol, "good", pos=pos)
                if pos.stop < pos.entry_price:
                    pos.stop = pos.entry_price * 1.002
                    pos.be_done = True
                    self.emit("stop", f"{c} stop moved to breakeven", "Partial profit taken, so the remainder is now a free trade.",
                              pos.symbol, "good", pos=pos)
            elif o.purpose == "exit":
                slip = (f.price / o.ref_price - 1) * 1e4 if o.ref_price else 0
                self.stats["slippage_usd"] += max(0.0, (o.ref_price - f.price) * f.qty)
                levels = f" across {f.levels} book levels" if f.levels > 1 else ""
                self.emit("fill", f"SOLD {qty_str(f.qty)} {c} @ {fmt_price(f.price)}",
                          f"Taker fee {money(f.fee)} · slippage {slip:+.1f} bps{levels}.", pos.symbol, "info", pos=pos)
            if pos.qty <= max(pos.bought_qty * 1e-6, 1e-12):
                reason = pos.exit_reason or ("TAKE_PROFIT" if o.purpose in ("take_profit", "partial") else "MANUAL")
                self._finalize(pos, reason, now)

    def _on_entry_update(self, u: OrderUpdate, pos: Position | None, now: float) -> None:
        o = u.order
        if pos is None:
            self._release(o.id)
            return
        c = coin(pos.symbol)
        if u.kind == "fill" and u.fill:
            f = u.fill
            self._release(o.id, f.qty * (o.limit_price or f.price) * (1 + f.fee / max(f.qty * f.price, 1e-12)))
            self.portfolio.buy(pos.symbol, f.qty, f.price, f.fee, now)
            pos.entry_price = (pos.entry_price * pos.bought_qty + f.price * f.qty) / (pos.bought_qty + f.qty)
            pos.bought_qty += f.qty
            pos.qty += f.qty
            pos.entry_fees += f.fee
            pos.hwm = max(pos.hwm, f.price)
            if not pos.opened_ts or pos.bought_qty == f.qty:
                pos.opened_ts = now
            slip = (f.price / o.ref_price - 1) * 1e4 if o.ref_price else 0
            pos.slippage_bps = slip
            self.stats["slippage_usd"] += max(0.0, (f.price - o.ref_price) * f.qty) if o.ref_price else 0
            partial = "" if u.done else f" (partial: {o.filled_qty / o.qty:.0%} filled)"
            levels = f" across {f.levels} book levels" if f.levels > 1 else ""
            self.emit("fill", f"FILLED {qty_str(f.qty)} {c} @ {fmt_price(f.price)}{partial}",
                      f"{f.liquidity.title()} fee {money(f.fee)} · slippage {slip:+.1f} bps{levels}.",
                      pos.symbol, "info", pos=pos)
            if u.done:
                self._release(o.id)
                self._activate(pos, now)
            return
        # cancelled / rejected
        self._release(o.id)
        if pos.status == "closing":
            return
        if pos.qty > 0:
            self.emit("order", f"{c} entry: keeping partial fill",
                      f"{o.reason or 'Order ended'}. Keeping the {qty_str(pos.qty)} {c} that filled.", pos.symbol, "info", pos=pos)
            self._activate(pos, now)
        else:
            self.positions.pop(pos.id, None)
            self.cooldown[pos.symbol] = now + self.p.signal_tf
            self.emit("order", f"{c} order cancelled", o.reason or "Order cancelled.", pos.symbol, "warn")

    def _record_sell(self, pos: Position, qty: float, price: float, fee: float, now: float) -> None:
        disposals = self.portfolio.sell(pos.symbol, qty, price, fee, now, pos.id)
        pos.qty -= qty
        pos.exit_qty += qty
        pos.exit_value += qty * price
        pos.exit_fees += fee
        for d in disposals:
            if d.term == "long":
                pos.gain_lt += d.gain
                self.ytd["lt"] += d.gain
            else:
                pos.gain_st += d.gain
                self.ytd["st"] += d.gain

    def _finalize(self, pos: Position, reason: str, now: float) -> None:
        for oid in (pos.entry_order_id, pos.tp_order_id, pos.partial_order_id, pos.exit_order_id):
            self._cancel(oid, now, "position closed")
        self.positions.pop(pos.id, None)
        cost = pos.bought_qty * pos.entry_price + pos.entry_fees
        proceeds = pos.exit_value - pos.exit_fees
        net = proceeds - cost
        fees = pos.entry_fees + pos.exit_fees
        gross = pos.exit_value - pos.bought_qty * pos.entry_price
        risk_usd = max(pos.bought_qty * pos.r_unit, 1e-9)
        r_mult = net / risk_usd
        exit_avg = pos.exit_value / max(pos.exit_qty, 1e-12)
        ts = self.tax_settings()
        after = taxlib.estimate(ts, self.ytd["st"], self.ytd["lt"])
        before = taxlib.estimate(ts, self.ytd["st"] - pos.gain_st, self.ytd["lt"] - pos.gain_lt)
        tax_est = after["total"] - before["total"]
        trade = {
            "id": pos.id, "bot": self.id, "symbol": pos.symbol, "strategy": STRATEGY_NAMES[pos.strategy],
            "opened": pos.opened_ts, "closed": now, "qty": pos.bought_qty, "entry": pos.entry_price,
            "exit": exit_avg, "gross": gross, "fees": fees, "net": net, "r": r_mult,
            "ret_pct": net / cost if cost else 0, "reason": reason, "reason_label": EXIT_LABELS.get(reason, reason),
            "confidence": pos.confidence, "headline": pos.headline, "tax": tax_est, "after_tax": net - tax_est,
            "term": "long" if pos.gain_lt and not pos.gain_st else "short", "max_r": pos.max_r,
            "stop": pos.initial_stop, "target": pos.target, "timeline": pos.events,
        }
        self.closed.appendleft(trade)
        self.new_trades.append(trade)
        s = self.stats
        s["trades"] += 1
        s["fees"] += fees
        s["volume"] += cost + pos.exit_value
        s["best"] = max(s["best"], net)
        s["worst"] = min(s["worst"], net)
        d = self.day
        d["trades"] += 1
        d["realized"] += net
        d["fees"] += fees
        if net >= 0:
            s["wins"] += 1
            s["gross_win"] += net
            d["wins"] += 1
            self.consec_losses = 0
        else:
            s["losses"] += 1
            s["gross_loss"] += -net
            d["losses"] += 1
            self.consec_losses += 1
        self.cooldown[pos.symbol] = now + self.p.cooldown_bars * self.p.signal_tf
        rs = self.strategy_r.setdefault(pos.strategy, [])
        rs.append(r_mult)
        del rs[:-8]

        c = coin(pos.symbol)
        mins = (now - pos.opened_ts) / 60
        held = f"{mins:.0f} min" if mins < 120 else f"{mins / 60:.1f} h"
        tax_txt = f"est. tax {money(tax_est)}" if tax_est >= 0 else f"est. tax saving {money(-tax_est)}"
        if net >= 0:
            title = f"WIN {c} {money(net, True)} ({r_mult:+.2f}R)"
            flavor = "Plan executed." if reason in ("TAKE_PROFIT", "TRAILING_STOP") else "Small win, capital protected."
        else:
            title = f"LOSS {c} {money(net, True)} ({r_mult:+.2f}R)"
            flavor = "Loss kept to the plan. On to the next one." if r_mult > -1.3 else "Took slippage on the stop. It happens."
        self.emit("close", title,
                  f"{EXIT_LABELS.get(reason, reason)} after {held}. {fmt_price(pos.entry_price)} to {fmt_price(exit_avg)} · "
                  f"fees {money(fees)} · {tax_txt} · after-tax {money(net - tax_est, True)}. {flavor}",
                  pos.symbol, "good" if net >= 0 else "bad", {"trade": {k: v for k, v in trade.items() if k != "timeline"}})
        if self.consec_losses in (3, 5):
            self.emit("risk", f"{self.consec_losses} losses in a row",
                      f"Cutting position size to {self.throttle():.0%} until I get a win. Staying in the game matters more than any single trade.",
                      level="warn")
        if len(rs) >= 5 and sum(rs[-5:]) < -3.5:
            self.benched[pos.strategy] = now + 6 * 3600
            self.emit("risk", f"Benching {STRATEGY_NAMES[pos.strategy]}",
                      f"Last 5 trades: {sum(rs[-5:]):+.1f}R. This strategy isn't working in current conditions. "
                      "Sitting it out for 6 hours.", level="warn")
            rs.clear()

    # ================================================================ views
    def _pos_pnl(self, pos: Position, price: float) -> dict:
        _, taker = self.venue.fee_rates(self.hub.now())
        if pos.bought_qty <= 0:
            return {"net": 0.0, "pct": 0.0, "r": 0.0}
        cost_open = pos.qty * pos.entry_price + pos.entry_fees * pos.qty / pos.bought_qty
        realized = pos.exit_value - pos.exit_fees - pos.exit_qty * pos.entry_price - pos.entry_fees * pos.exit_qty / pos.bought_qty
        net = pos.qty * price * (1 - taker) - cost_open + realized
        basis = pos.bought_qty * pos.entry_price + pos.entry_fees
        risk_usd = max(pos.bought_qty * pos.r_unit, 1e-9)
        return {"net": net, "pct": net / basis if basis else 0, "r": net / risk_usd}

    def positions_json(self) -> list[dict]:
        out = []
        for pos in self.positions.values():
            price = self.hub.price(pos.symbol)
            pnl = self._pos_pnl(pos, price)
            out.append({
                "id": pos.id, "bot": self.id, "symbol": pos.symbol, "strategy": STRATEGY_NAMES[pos.strategy],
                "status": pos.status, "qty": pos.qty, "entry": pos.entry_price or pos.planned_entry,
                "price": price, "stop": pos.stop, "initial_stop": pos.initial_stop, "target": pos.target,
                "partial": pos.partial_price, "partial_done": pos.partial_done, "opened": pos.opened_ts,
                "pnl": pnl["net"], "pnl_pct": pnl["pct"], "r": pnl["r"], "confidence": pos.confidence,
                "headline": pos.headline, "reasons": pos.reasons, "trailing": pos.trailing, "be": pos.be_done,
                "value": pos.qty * price, "timeline": pos.events[-8:],
            })
        return out

    def summary_json(self, now: float) -> dict:
        eq = self.equity()
        s, d = self.stats, self.day or {"start_equity": eq, "realized": 0, "fees": 0, "trades": 0, "wins": 0,
                                         "losses": 0, "ytd_st": 0, "ytd_lt": 0}
        ts = self.tax_settings()
        tax_ytd = taxlib.estimate(ts, self.ytd["st"], self.ytd["lt"])
        tax_day_start = taxlib.estimate(ts, d["ytd_st"], d["ytd_lt"])
        tax_today = tax_ytd["total"] - tax_day_start["total"]
        today_pnl = eq - d["start_equity"]
        realized_all = s["gross_win"] - s["gross_loss"]
        pf = s["gross_win"] / s["gross_loss"] if s["gross_loss"] > 0 else (None if not s["gross_win"] else 99.0)
        return {
            "id": self.id, "name": self.p.name, "label": self.p.label, "color": self.p.color,
            "tagline": self.p.tagline, "equity": eq, "cash": self.portfolio.cash, "start": self.starting_balance,
            "total_return": eq / self.starting_balance - 1, "status": self.status_text,
            "halted": self.halted, "paused": self.paused, "open": self.open_count(),
            "exposure": self.exposure(), "throttle": self.throttle(),
            "benched": [STRATEGY_NAMES[k] for k, v in self.benched.items() if v > now],
            "today": {"pnl": today_pnl, "pnl_pct": today_pnl / d["start_equity"] if d["start_equity"] else 0,
                      "realized": d["realized"], "fees": d["fees"], "trades": d["trades"], "wins": d["wins"],
                      "losses": d["losses"], "tax": tax_today, "after_tax": today_pnl - tax_today,
                      "start_equity": d["start_equity"],
                      "limit_used": max(0.0, -today_pnl / d["start_equity"] / self.p.daily_loss_limit) if d["start_equity"] else 0},
            "all": {"trades": s["trades"], "wins": s["wins"], "losses": s["losses"],
                    "win_rate": s["wins"] / s["trades"] if s["trades"] else None, "profit_factor": pf,
                    "fees": s["fees"], "realized": realized_all, "best": s["best"], "worst": s["worst"],
                    "max_dd": s["max_dd"], "volume": s["volume"], "slippage": s["slippage_usd"],
                    "streak": -self.consec_losses},
            "tax": tax_ytd,
            "profile": {"risk_per_trade": self.p.risk_per_trade, "daily_loss_limit": self.p.daily_loss_limit,
                        "max_open": self.p.max_open, "symbols": [coin(x) for x in self.p.symbols],
                        "signal_tf": TF_LABEL[self.p.signal_tf], "trend_tf": TF_LABEL[self.p.trend_tf],
                        "entry_order": self.p.entry_order, "min_net_rr": self.p.min_net_rr,
                        "min_confidence": self.p.min_confidence,
                        "strategies": [STRATEGY_NAMES[x] for x in self.p.strategies]},
        }

    # ========================================================= persistence
    def dump(self) -> dict:
        return {
            "portfolio": self.portfolio.dump(),
            "positions": [asdict(p) for p in self.positions.values() if p.qty > 0],
            "stats": self.stats, "day": self.day, "ytd": self.ytd, "halted": self.halted, "paused": self.paused,
            "cooldown": self.cooldown, "benched": self.benched, "strategy_r": self.strategy_r,
            "consec_losses": self.consec_losses, "closed": list(self.closed)[:100],
        }

    def load(self, d: dict, now: float) -> None:
        self.portfolio.load(d["portfolio"])
        self.stats.update(d.get("stats", {}))
        self.day = d.get("day", {})
        self.ytd = d.get("ytd", self.ytd)
        self.halted = d.get("halted", False)
        self.paused = d.get("paused", False)
        self.cooldown = d.get("cooldown", {})
        self.benched = d.get("benched", {})
        self.strategy_r = d.get("strategy_r", {})
        self.consec_losses = d.get("consec_losses", 0)
        self.closed = deque(d.get("closed", []), maxlen=300)
        for pd in d.get("positions", []):
            pos = Position(**pd)
            pos.entry_order_id = pos.tp_order_id = pos.partial_order_id = pos.exit_order_id = ""
            pos.status = "open"
            pos.qty = self.portfolio.qty(pos.symbol)
            if pos.qty <= 0:
                continue
            self.positions[pos.id] = pos
            self._place_exit_orders(pos, now)
