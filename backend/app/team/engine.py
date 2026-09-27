"""QUORUM: three trading agents that share one account and a desk that manages them.

  ATLAS   regime strategist. Bull market: rides BTC. Otherwise: trades breakouts in both
          directions (shorts the breakdowns) and runs a delta-neutral funding carry.
  ORACLE  machine learning. Long AND short 14-day forecasts for 10 coins every hour; takes
          only its top-10% ideas (longs in bull markets, shorts in bear markets). Idle
          capital runs the carry trade.
  NOVA    adaptive learner. Shadow-tracks every strategy on the team (its teammates'
          included) and each week backs whatever has worked over the last 90 days,
          risk-weighted. Nothing working -> cash.
  DESK    at the start of every month re-allocates capital between the three by their
          last-90-day risk-adjusted returns (each keeps 20-60%). Nets the agents' orders
          against each other so opposite trades never pay fees, enforces margin/exposure
          limits and applies the news desk's vetoes.

Daily decisions are made right after the UTC daily close; ORACLE decides every hour.
All daily logic is the SAME code as the research simulation (app.team.research).
"""
from __future__ import annotations

import itertools
import logging
import math
import time
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from ..accounting.tax import TaxSettings
from ..execution.fees import FeeTracker
from .book import (CONTRACT, MARGIN, PERP_FEE, PERP_ID, PERP_SLIP, PERP_SLIP_ALT, STOP_SLIP, STOP_SLIP_ALT, Book,
                   perp_name)
from .research import NOVA_UNIVERSE, CostModel, DeskState, compute_all

log = logging.getLogger("team.engine")
ET = ZoneInfo("America/New_York")
DAY = 86400
AGENTS = ("ATLAS", "ORACLE", "NOVA")
AGENT_INFO = {
    "ATLAS": {"color": "#7dd3fc", "role": "Regime strategist",
              "about": "Reads the market regime and switches playbook: rides BTC in bull markets; "
                       "trades breakouts both ways and runs funding carry otherwise."},
    "ORACLE": {"color": "#c4b5fd", "role": "Machine learning",
               "about": "Two gradient-boosted models forecast 14-day long and short outcomes for 10 coins "
                        "every hour. Trades only its top-10% ideas."},
    "NOVA": {"color": "#fcd34d", "role": "Adaptive learner",
             "about": "Shadow-tracks every strategy on the team and each week backs what has worked "
                      "over the last 90 days, risk-weighted."},
}
STRAT_LABEL = {"btc_regime": "BTC trend", "trend": "BTC/ETH trend", "rotation": "Momentum rotation",
               "breakout": "Breakouts", "carry": "Funding carry", "oracle_long": "ORACLE longs",
               "oracle_short": "ORACLE shorts"}
ORACLE_SIZE = 0.20
ORACLE_MAX = 5               # per side
DAILY_BAND = 0.05            # daily rebalance: fix drift above 5% of the target
MIN_TRADE_USD = 20.0
_ids = itertools.count(1)


def et_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")


def et_month(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m")


def coin(s: str) -> str:
    return s.split("-")[0]


def fmt_px(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}".rstrip("0").rstrip(".") if p < 10 else f"{p:,.2f}"
    return f"{p:.5f}"


def _freeze(x):
    return tuple(_freeze(v) for v in x) if isinstance(x, (list, tuple)) else x


def usd(x: float, sign: bool = False) -> str:
    s = f"${abs(x):,.2f}"
    return (("+" if x >= 0 else "−") + s) if sign else (("−" if x < 0 else "") + s)


class Quorum:
    def __init__(self, symbols: list[str], balance: float, prices, bars, forecaster, news=None, db=None,
                 store=None, alt=None, desk_source=None, costs: CostModel | None = None, clock=time.time):
        """prices(): dict symbol -> (bid, ask, last). bars(symbol, bar_t) -> (o,h,l,c) or None."""
        self.symbols = symbols
        self.prices_fn = prices
        self.bars = bars
        self.fc = forecaster
        self.news = news
        self.db = db
        self.store = store
        self.alt = alt
        self.desk_source = desk_source     # replay: precomputed DeskState
        self.fixed_costs = costs
        self.clock = clock
        self.balance = balance
        from ..config import TAX_FILING_STATUS, TAX_OTHER_INCOME, TAX_STATE
        self.tax = TaxSettings(TAX_FILING_STATUS, TAX_OTHER_INCOME, TAX_STATE)
        self.reset(balance)

    # ================================================================ state
    def reset(self, balance: float) -> None:
        self.book = Book(balance)
        self.fees = FeeTracker("auto")
        self.desk_w = {a: 1 / 3 for a in AGENTS}
        self.targets: dict[str, dict[str, float]] = {a: {} for a in AGENTS}   # weights of agent capital
        self.fixed_q: dict[str, dict[str, float]] = {a: {} for a in AGENTS}   # fixed quantities (ORACLE trades)
        self._last_intent: dict[str, tuple] = {}
        self._prev_want: dict[str, float] = {}
        self.agent_q: dict[str, dict[str, float]] = {a: {} for a in AGENTS}
        self.agent_pnl = {a: {"all": 0.0, "fees": 0.0, "funding": 0.0} for a in AGENTS}
        self.agent_mode = {a: "Starting up" for a in AGENTS}
        self.agent_detail: dict[str, list] = {a: [] for a in AGENTS}
        self.oracle_trades: list[dict] = []
        self.oracle_closed: deque[dict] = deque(maxlen=200)
        self.nova_picks: dict[str, float] = {}
        self.nova_sharpe: dict[str, float] = {}
        self.regime = {"btc_on": None}
        self.events: deque[dict] = deque(maxlen=600)
        self.outbox: list[dict] = []
        self.fills: deque[dict] = deque(maxlen=300)
        self.equity_hist: deque[tuple] = deque(maxlen=50_000)
        self.day_pnl: dict[str, float] = {}
        self.day_start: dict[str, float] = {}
        self.month_start: dict[str, float] = {}
        self.agent_day_start: dict[str, dict[str, float]] = {}
        self.agent_month_start: dict[str, dict[str, float]] = {}
        self.last_px: dict[str, float] = {}
        self.last_hour_bar = 0
        self.last_day = 0
        self.last_sample = 0.0
        self.started = self.clock()
        self.state: DeskState | None = None
        self.costs = self.fixed_costs or CostModel.for_spot_fee(0.006, 0.012)
        self.pending_desk: DeskState | None = None
        self._last_scale = 1.0
        self.netting_saved = 0.0

    # ============================================================ helpers
    def px(self) -> dict[str, float]:
        return {s: q[2] for s, q in self.prices_fn().items() if q and q[2] > 0}

    def emit(self, kind: str, agent: str, title: str, text: str = "", level: str = "info",
             symbol: str | None = None, data: dict | None = None) -> None:
        ev = {"id": f"{int(time.time() * 1000):x}-{next(_ids)}", "ts": self.clock(), "kind": kind, "agent": agent,
              "bot": agent,
              "title": title, "text": text, "level": level, "symbol": symbol, "data": data or {}}
        self.events.append(ev)
        self.outbox.append(ev)

    def equity(self) -> float:
        return self.book.equity(self.px())

    def _cur_qty(self, key: str) -> float:
        venue, s = key.split(":")
        if venue == "spot":
            return self.book.spot_qty(s)
        p = self.book.perps.get(s)
        return p.qty if p else 0.0

    def _cost_model(self) -> CostModel:
        if self.fixed_costs:
            return self.fixed_costs
        t = self.fees.tier(self.clock())
        return CostModel.for_spot_fee(t.maker, t.taker)

    # ============================================================ clock
    def step(self, now: float | None = None) -> None:
        now = now or self.clock()
        px = self.px()
        if not px:
            return
        self._attribute_marks(px)
        self._period_marks(now)
        day = int(now // DAY) - 1                     # most recent COMPLETED UTC day
        if day > self.last_day and self._day_ready(day, now):
            self.on_day(day, now)
        if self.fc is not None and self.fc.ready and self.regime.get("btc_on") is not None:
            bar = self.fc.latest_bar(now)
            if bar and bar > self.last_hour_bar:
                self.on_hour(bar, now)
        self._live_barriers(px, now)
        if now - self.last_sample >= (900 if self.desk_source is None else 3600):
            self.last_sample = now
            self._sample(now, px)

    def _day_ready(self, day: int, now: float) -> bool:
        if self.desk_source is not None:
            return True
        st = self.pending_desk            # computed off the event loop by the server after each close
        return st is not None and len(st.d.days) and int(st.d.days[-1]) >= day

    # ============================================================ daily
    def _desk(self) -> DeskState | None:
        return self.desk_source if self.desk_source is not None else self.pending_desk

    def compute_desk(self) -> DeskState | None:
        """Heavy (seconds): run in a worker thread. Uses ORACLE's live forecast history."""
        try:
            fc = self.fc
            panels = (fc.panels.get("long"), fc.panels.get("short")) if getattr(fc, "panels", None) else None
            return compute_all(self.store, self.alt, fc.P.get("long"), fc.P.get("short"), panels=panels,
                               costs=self._cost_model())
        except Exception:  # noqa: BLE001
            log.exception("desk computation failed")
            return None

    def on_day(self, day: int, now: float) -> None:
        st = self._desk()
        if st is None:
            return
        idx = np.where(st.d.days == day)[0]
        if not len(idx):
            return
        self.last_day = day
        self.state = st
        i = int(idx[0])
        before = {a: dict(self.targets[a]) for a in AGENTS}
        self.costs = self._cost_model()
        c = self.costs
        syms = st.d.symbols
        btc_on = bool(st.btc_on[i])
        b = syms.index("BTC-USD")
        from .research import sma
        s200 = float(sma(st.d.C[:, [b]], 200)[i, 0])
        was = self.regime.get("btc_on")
        self.regime = {"btc_on": btc_on, "btc_close": float(st.d.C[i, b]), "btc_sma200": s200,
                       "fear_greed": float(st.d.G[i]) if np.isfinite(st.d.G[i]) else None, "day": day}
        if was is None or was != btc_on:
            self.emit("regime", "ATLAS", f"Market regime: {'BULL' if btc_on else 'RISK-OFF'}",
                      f"BTC closed at {fmt_px(self.regime['btc_close'])} vs its 200-day average {fmt_px(s200)}.",
                      "good" if btc_on else "warn")
        long_key = "spot" if c.long_venue == "spot" else "perp"

        def row(W, venue):
            return {f"{venue}:{syms[j]}": float(W[i, j]) for j in range(len(syms)) if abs(W[i, j]) > 1e-9}

        carry = {}
        for j in range(len(syms)):
            cw = float(st.carry_w[i, j])
            if cw > 1e-9:
                carry[f"spot:{syms[j]}"] = carry.get(f"spot:{syms[j]}", 0) + cw
                carry[f"perp:{syms[j]}"] = carry.get(f"perp:{syms[j]}", 0) - cw

        # ---- ATLAS
        if btc_on:
            atlas = {f"{long_key}:BTC-USD": 1.0}
            self.agent_mode["ATLAS"] = "Bull mode · long BTC"
            self.agent_detail["ATLAS"] = [["BTC trend", 1.0]]
        else:
            atlas = {k: 0.5 * v for k, v in row(st.targets["breakout"], "perp").items()}
            for k, v in carry.items():
                atlas[k] = atlas.get(k, 0) + 0.5 * v
            nb = sum(1 for k in atlas if k.startswith("perp:") and atlas[k] and k.replace("perp", "spot") not in atlas)
            self.agent_mode["ATLAS"] = (f"Risk-off · {nb} breakout trade{'s' if nb != 1 else ''}"
                                        + (" + carry" if carry else "") if (nb or carry) else "Risk-off · cash")
            self.agent_detail["ATLAS"] = [["Breakouts", 0.5], ["Funding carry" if c.carry_on else "Cash", 0.5]]
        self._set_targets("ATLAS", atlas)

        # ---- NOVA
        w = st.nova_w[i]
        picks = {NOVA_UNIVERSE[k]: float(w[k]) for k in range(len(NOVA_UNIVERSE)) if w[k] > 1e-6}
        from .research import sharpe
        R = np.stack([st.lib[n] for n in NOVA_UNIVERSE], axis=1)[max(0, i - 90):i + 1]
        self.nova_sharpe = {n: round(sharpe(R[:, k]), 2) for k, n in enumerate(NOVA_UNIVERSE)}
        if picks != self.nova_picks:
            txt = ", ".join(f"{STRAT_LABEL[k]} {v * 100:.0f}%" for k, v in sorted(picks.items(), key=lambda x: -x[1]))
            dropped = [STRAT_LABEL[k] for k in self.nova_picks if k not in picks]
            self.emit("nova", "NOVA", "Weekly strategy review",
                      (f"Backing: {txt}." if picks else "Nothing has a positive 90-day record. Going to cash.")
                      + (f" Dropped: {', '.join(dropped)}." if dropped else ""), "info",
                      data={"picks": picks, "sharpe": self.nova_sharpe})
            self.nova_picks = picks
        self.agent_mode["NOVA"] = ("Backing " + " + ".join(STRAT_LABEL[k] for k in sorted(picks, key=lambda k: -picks[k])[:2])
                                   + (f" +{len(picks) - 2}" if len(picks) > 2 else "")) if picks else "Cash · waiting"
        self.agent_detail["NOVA"] = [[STRAT_LABEL[k], v] for k, v in sorted(picks.items(), key=lambda x: -x[1])]
        self._nova_base = {}
        for name, wt in picks.items():
            if name in ("oracle_long", "oracle_short"):
                continue
            if name == "carry":
                src = carry
            else:
                src = row(st.targets[name], long_key if name == "btc_regime" else "perp")
            for k, v in src.items():
                self._nova_base[k] = self._nova_base.get(k, 0) + wt * v
        self._nova_oracle = {k: v for k, v in picks.items() if k.startswith("oracle")}
        self._carry_row = carry
        self._refresh_oracle_targets()

        # ---- DESK (monthly)
        dw = {a: float(st.desk_w[i, k]) for k, a in enumerate(AGENTS)}
        if any(abs(dw[a] - self.desk_w[a]) > 1e-6 for a in AGENTS):
            A = np.stack([st.agents[a] for a in AGENTS], axis=1)[max(0, i - 90):i + 1]
            sh = {a: round(sharpe(A[:, k]), 2) for k, a in enumerate(AGENTS)}
            self.emit("desk", "DESK", f"{datetime.fromtimestamp(now, ET).strftime('%B')} capital allocation",
                      " · ".join(f"{a} {dw[a] * 100:.0f}% (90-day Sharpe {sh[a]:+.2f})" for a in AGENTS), "info",
                      data={"weights": dw, "sharpe": sh})
            self.desk_w = dw
        for a in ("ATLAS", "NOVA"):
            self._announce(a, before[a], self.targets[a])
        self.rebalance(now, reason="daily")
        eq = self.equity()
        m0 = self.month_start.get(et_month(now), eq)
        ex = self.book.exposure(self.px())
        self.emit("note", "DESK", "Daily close",
                  f"Net liquidation {usd(eq)} · month to date {usd(eq - m0, True)} ({(eq / m0 - 1) * 100:+.2f}%) · "
                  f"regime {'bull' if btc_on else 'risk-off'} · net exposure {ex['net'] * 100:+.0f}% "
                  f"(gross {ex['gross'] * 100:.0f}%).", "info")

    def _announce(self, agent: str, old: dict[str, float], new: dict[str, float]) -> None:
        """Short, factual description of what changed in an agent's plan."""
        def name(k):
            v, s = k.split(":")
            return s if v == "spot" else perp_name(s)
        parts = []
        for k in sorted(set(old) | set(new), key=lambda x: -abs(new.get(x, 0))):
            a, b = old.get(k, 0.0), new.get(k, 0.0)
            if abs(a - b) < 0.02:
                continue
            if abs(b) < 1e-9:
                parts.append(f"exit {name(k)}")
            elif abs(a) < 1e-9 or (a > 0) != (b > 0):
                parts.append(f"{'long' if b > 0 else 'short'} {name(k)} {abs(b) * 100:.0f}%")
            else:
                parts.append(f"{name(k)} {abs(a) * 100:.0f}% → {abs(b) * 100:.0f}%")
        if parts:
            self.emit("plan", agent, f"{agent} plan", " · ".join(parts[:6]) + (" …" if len(parts) > 6 else ""),
                      "info", data={"old": old, "new": new})

    # ============================================================ ORACLE
    def on_hour(self, bar: int, now: float) -> None:
        self.last_hour_bar = bar
        px = self.px()
        self._funding(bar, px)
        for tr in list(self.oracle_trades):
            if bar + 3600 >= tr["expires"]:
                self._close_oracle(tr, "TIME", px.get(tr["symbol"], tr["entry"]), now)
        on = self.regime.get("btc_on")
        if on is None:          # the desk hasn't published today's regime yet
            self.rebalance(now, reason="hourly")
            return
        side = "long" if on else "short"
        thr = self.fc.threshold(side, bar)
        cands = []
        held = {t["symbol"] for t in self.oracle_trades}
        for s in self.symbols:
            f = self.fc.forecast(s, bar)
            if not f or f.get(side) is None or s in held:
                continue
            if f[side] >= thr and math.isfinite(thr) and np.isfinite(f["dvol"]):
                cands.append((f[side], s, f))
        cands.sort(reverse=True)
        opened_before = len(self.oracle_trades)
        eq = self.book.equity(px)
        for pred, s, f in cands:
            if sum(1 for t in self.oracle_trades if t["side"] == side) >= ORACLE_MAX:
                break
            if side == "long" and self.news and self.news.blocked(s, now):
                self.emit("news", "ORACLE", f"Skipped {coin(s)} long", "The news desk has a negative-headline veto on it.",
                          "warn", s)
                continue
            p = px.get(s)
            if not p:
                continue
            dv = f["dvol"]
            sg = 1 if side == "long" else -1
            nova_share = getattr(self, "_nova_oracle", {}).get(f"oracle_{side}", 0.0)
            tr = {"id": f"orc-{bar}-{coin(s)}", "symbol": s, "side": side, "weight": ORACLE_SIZE, "entry": p,
                  "stop": p * (1 - sg * 2 * dv), "target": p * (1 + sg * 4 * dv), "opened": now,
                  "expires": bar + 3600 + 336 * 3600, "forecast": pred, "thr": thr, "reasons": f.get("reasons", []),
                  # fixed size for the life of the trade (no hourly resizing)
                  "qty": sg * ORACLE_SIZE * eq * self.desk_w["ORACLE"] / p,
                  "nova_qty": sg * nova_share * ORACLE_SIZE * eq * self.desk_w["NOVA"] / p}
            self.oracle_trades.append(tr)
            self.emit("signal", "ORACLE", f"{'LONG' if side == 'long' else 'SHORT'} {perp_name(s)} @ {fmt_px(p)}",
                      f"Forecast {pred:+.2f}R vs bar {thr:+.2f}R · stop {fmt_px(tr['stop'])} · target {fmt_px(tr['target'])}"
                      f" · 14-day window · {ORACLE_SIZE * 100:.0f}% of ORACLE capital. "
                      + ("; ".join(tr["reasons"][:2]) + "." if tr["reasons"] else ""), "info", s,
                      data={"trade": tr})
        if len(self.oracle_trades) == opened_before:
            best = None
            for s in self.symbols:
                f = self.fc.forecast(s, bar)
                if f and f.get(side) is not None and (best is None or f[side] > best[1]):
                    best = (s, f[side])
            key = (best[0], side) if best else None
            due = now - getattr(self, "_last_scan_note", 0) >= 4 * 3600 or key != getattr(self, "_last_scan_key", None)
            if best and math.isfinite(thr) and due:
                self._last_scan_note, self._last_scan_key = now, key
                n = sum(1 for t in self.oracle_trades if t["side"] == side)
                why = "book full" if n >= ORACLE_MAX else "below the bar"
                self.emit("scan", "ORACLE", f"Scan · best {side} {coin(best[0])} {best[1]:+.2f}R",
                          f"Bar {thr:+.2f}R · {why} · {len(self.oracle_trades)} open.", "info", best[0])
        self._refresh_oracle_targets()
        self.rebalance(now, reason="hourly")

    def _live_barriers(self, px: dict[str, float], now: float) -> None:
        """Stops and targets. Live quotes are continuous, so we fill at the market. Replays only
        see a few ticks per hour, so a crossed level fills where the resting order would have:
        targets at the target, stops at the stop minus extra slippage."""
        override: dict[str, float] = {}
        for tr in list(self.oracle_trades):
            s = tr["symbol"]
            p = px.get(s)
            if not p:
                continue
            sg = 1 if tr["side"] == "long" else -1
            if sg * (p - tr["stop"]) <= 0:
                hit = "STOP"
                fill = tr["stop"] * (1 - sg * STOP_SLIP.get(s, STOP_SLIP_ALT))
            elif sg * (p - tr["target"]) >= 0:
                hit, fill = "TARGET", tr["target"]
            else:
                continue
            if self.desk_source is None:
                fill = p
            self._close_oracle(tr, hit, fill, now)
            override[s] = fill
        if override:
            self._refresh_oracle_targets()
            self.rebalance(now, reason="stop/target", override=override)

    def _close_oracle(self, tr: dict, why: str, price: float, now: float) -> None:
        if tr not in self.oracle_trades:
            return
        self.oracle_trades.remove(tr)
        sg = 1 if tr["side"] == "long" else -1
        r = sg * (price / tr["entry"] - 1)
        R = r / abs(tr["entry"] - tr["stop"]) * tr["entry"]
        tr = {**tr, "closed": now, "exit": price, "ret": r, "R": R, "why": why}
        self.oracle_closed.appendleft(tr)
        label = {"STOP": "Stop hit", "TARGET": "Target hit", "TIME": "14-day window ended"}[why]
        self.emit("signal", "ORACLE", f"CLOSE {perp_name(tr['symbol'])} {tr['side']} · {label}",
                  f"{fmt_px(tr['entry'])} → {fmt_px(price)} ({r * 100:+.2f}%, {R:+.2f}R).",
                  "good" if r > 0 else "bad", tr["symbol"], data={"trade": tr})
        self._refresh_oracle_targets()

    def _refresh_oracle_targets(self) -> None:
        tg: dict[str, float] = {}
        fq: dict[str, float] = {}
        nq: dict[str, float] = {}
        for tr in self.oracle_trades:
            k = f"perp:{tr['symbol']}"
            fq[k] = fq.get(k, 0) + tr["qty"]
            if tr.get("nova_qty"):
                nq[k] = nq.get(k, 0) + tr["nova_qty"]
        self.fixed_q["ORACLE"] = fq
        self.fixed_q["NOVA"] = nq
        used = min(1.0, sum(tr["weight"] for tr in self.oracle_trades))
        idle = max(0.0, 1 - used)
        for k, v in getattr(self, "_carry_row", {}).items():
            tg[k] = tg.get(k, 0) + idle * v
        self._set_targets("ORACLE", tg)
        n = len(self.oracle_trades)
        nl = sum(1 for t in self.oracle_trades if t["side"] == "long")
        self.agent_mode["ORACLE"] = (f"{n} trade{'s' if n != 1 else ''} · {nl} long / {n - nl} short" if n
                                     else "No edge right now · " + ("carry" if getattr(self, "_carry_row", {}) else "cash"))
        self.agent_detail["ORACLE"] = [["ML trades", used], ["Funding carry" if self._carry_row else "Cash", idle]] \
            if hasattr(self, "_carry_row") else [["ML trades", used]]
        # NOVA's weight-based book (its copies of ORACLE trades are fixed quantities, above)
        self._set_targets("NOVA", dict(getattr(self, "_nova_base", {})))

    def _set_targets(self, agent: str, tg: dict[str, float]) -> None:
        self.targets[agent] = {k: v for k, v in tg.items() if abs(v) > 1e-9}

    # ============================================================ execution
    def rebalance(self, now: float, reason: str = "", override: dict[str, float] | None = None) -> None:
        quotes = dict(self.prices_fn())
        for s, p in (override or {}).items():
            quotes[s] = (p, p, p)
        px = {s: q[2] for s, q in quotes.items() if q and q[2] > 0}
        eq = self.book.equity(px)
        if eq <= 0:
            return
        want: dict[str, float] = {}
        per_agent: dict[str, dict[str, float]] = {}
        intent: dict[str, tuple] = {}
        for a in AGENTS:
            cap = eq * self.desk_w[a]
            per_agent[a] = {}
            for k, w in self.targets[a].items():
                s = k.split(":")[1]
                if s not in px:
                    continue
                q = cap * w / px[s]
                per_agent[a][k] = per_agent[a].get(k, 0.0) + q
                want[k] = want.get(k, 0.0) + q
                intent[k] = intent.get(k, ()) + ((a, "w", round(w, 6), round(self.desk_w[a], 6)),)
            for k, q in self.fixed_q[a].items():
                per_agent[a][k] = per_agent[a].get(k, 0.0) + q
                want[k] = want.get(k, 0.0) + q
                intent[k] = intent.get(k, ()) + ((a, "q", round(q, 10)),)
        # ---- risk officer
        perp_gross = sum(abs(q) * px[k.split(":")[1]] for k, q in want.items() if k.startswith("perp:"))
        spot_total = sum(max(q, 0) * px[k.split(":")[1]] for k, q in want.items() if k.startswith("spot:"))
        scale = 1.0
        if perp_gross > 2.0 * eq:
            scale = min(scale, 2.0 * eq / perp_gross)
        budget = 0.98 * eq - MARGIN * perp_gross * scale
        if spot_total * scale > budget and spot_total > 0:
            scale = min(scale, max(budget, 0) / spot_total)
        if scale < 0.999:
            want = {k: q * scale for k, q in want.items()}
            per_agent = {a: {k: q * scale for k, q in d.items()} for a, d in per_agent.items()}
            if abs(scale - self._last_scale) > 0.05:
                self.emit("risk", "DESK", "Exposure trimmed", f"Book scaled to {scale * 100:.0f}% to stay inside "
                          "margin and cash limits.", "warn")
        self._last_scale = scale
        if self.news:
            for k in list(want):
                venue, s = k.split(":")
                cur = self._cur_qty(k)
                hedged = venue == "spot" and want.get(f"perp:{s}", 0) < 0
                if want[k] > max(cur, 0) and not hedged and self.news.blocked(s, now):
                    want[k] = max(cur, 0)
        tier_now = self.fees.tier(now)
        for k in set(want) | set(self._prev_want):
            s = k.split(":")[1]
            if s not in px:
                continue
            gross = sum(abs(per_agent[a].get(k, 0.0) - self.agent_q.get(a, {}).get(k, 0.0)) for a in AGENTS)
            net = abs(want.get(k, 0.0) - self._prev_want.get(k, 0.0))
            rate = PERP_FEE if k.startswith("perp:") else tier_now.taker
            self.netting_saved += max(0.0, gross - net) * px[s] * rate
        self._prev_want = dict(want)
        self._attribute_targets(per_agent)
        keys = set(want) | {f"spot:{s}" for s in self.book.spot.lots} | {f"perp:{s}" for s in self.book.perps}
        tier = self.fees.tier(now)
        for k in sorted(keys):
            venue, s = k.split(":")
            if s not in quotes:
                continue
            bid, ask, last = quotes[s]
            cur = self._cur_qty(k)
            tgt = want.get(k, 0.0)
            changed = intent.get(k, ()) != self._last_intent.get(k, ())
            # Only trade what someone decided: intraday, positions whose plan didn't change are left
            # alone (no churn from price drift). The daily rebalance fixes drift beyond a 5% band.
            if not changed:
                if reason != "daily":
                    continue
                if tgt != 0 and abs(tgt - cur) <= DAILY_BAND * abs(tgt):
                    continue
            self._last_intent[k] = intent.get(k, ())
            if venue == "perp":
                cs = CONTRACT[s]
                tgt = round(tgt / cs) * cs
                diff = tgt - cur
                if abs(diff) < cs * 0.5:
                    continue
                slip = PERP_SLIP.get(s, PERP_SLIP_ALT)
                price = (ask if diff > 0 else bid) * (1 + slip if diff > 0 else 1 - slip)
                f = self.book.trade_perp(s, diff, price, now)
            else:
                diff = tgt - cur
                if abs(diff) * last < MIN_TRADE_USD or (tgt > 0 and abs(diff) * last < 0.0025 * eq):
                    if not (tgt == 0 and cur > 0):
                        continue
                if diff > 0:
                    diff = min(diff, max(self.book.cash - MARGIN * self.book.perp_gross(px), 0) / (ask * (1 + tier.taker)))
                    if diff * ask < MIN_TRADE_USD:
                        continue
                f = self.book.trade_spot(s, diff, ask if diff > 0 else bid, tier.taker, now)
                self.fees.record(now, f.qty * f.price)
            self._charge_fees(k, f.fee)
            fj = f.to_json()
            fj["reason"] = reason
            self.fills.appendleft(fj)
            inst = fj["instrument"]
            size = f"{fj['contracts']} contract{'s' if fj['contracts'] != 1 else ''}" if venue == "perp" else f"{f.qty:.6f}".rstrip("0").rstrip(".")
            self.emit("fill", "DESK", f"{f.side.upper()} {size} {inst} @ {fmt_px(f.price)}",
                      f"Notional {usd(f.qty * f.price)} · fee {usd(f.fee)}"
                      + (f" · realized {usd(f.realized, True)}" if f.realized else "") + f" · {reason}",
                      "info", s, data={"fill": fj})

    def _attribute_targets(self, per_agent: dict[str, dict[str, float]]) -> None:
        """Record each agent's desired quantities; count what netting saved."""
        total_abs = {}
        net = {}
        for a, d in per_agent.items():
            for k, q in d.items():
                total_abs[k] = total_abs.get(k, 0) + abs(q)
                net[k] = net.get(k, 0) + q
        self._agent_share = {}
        for k in total_abs:
            if total_abs[k] > 0:
                self._agent_share[k] = {a: abs(per_agent[a].get(k, 0)) / total_abs[k] for a in AGENTS}
        self.agent_q = per_agent

    def _charge_fees(self, key: str, fee: float) -> None:
        share = getattr(self, "_agent_share", {}).get(key) or {a: 1 / 3 for a in AGENTS}
        for a, x in share.items():
            self.agent_pnl[a]["fees"] += fee * x
            self.agent_pnl[a]["all"] -= fee * x

    def _attribute_marks(self, px: dict[str, float]) -> None:
        for a in AGENTS:
            pnl = 0.0
            for k, q in self.agent_q.get(a, {}).items():
                s = k.split(":")[1]
                if s in px and s in self.last_px:
                    pnl += q * (px[s] - self.last_px[s])
            self.agent_pnl[a]["all"] += pnl
        self.last_px = dict(px)

    def _funding(self, bar: int, px: dict[str, float]) -> None:
        if self.alt is None:
            return
        total = 0.0
        for s in list(self.book.perps):
            rate = float(self.alt.hourly_funding(s, np.array([bar]))[0])
            total += self.book.accrue_funding(s, rate, px.get(s, self.book.perps[s].avg), bar + 3600)
            for a in AGENTS:
                q = self.agent_q.get(a, {}).get(f"perp:{s}", 0.0)
                pay = q * px.get(s, 0) * rate
                self.agent_pnl[a]["funding"] += pay
                self.agent_pnl[a]["all"] -= pay
        self._funding_hour = total

    # ============================================================ reporting
    def _period_marks(self, now: float) -> None:
        eq = self.equity()
        d, m = et_day(now), et_month(now)
        if d not in self.day_start:
            self.day_start[d] = eq
            self.agent_day_start[d] = {a: self.agent_pnl[a]["all"] for a in AGENTS}
            if len(self.day_start) > 400:
                for k in sorted(self.day_start)[:-400]:
                    self.day_start.pop(k, None)
                    self.agent_day_start.pop(k, None)
        if m not in self.month_start:
            self.month_start[m] = eq
            self.agent_month_start[m] = {a: self.agent_pnl[a]["all"] for a in AGENTS}
        self.day_pnl[d] = eq - self.day_start[d]

    def _sample(self, now: float, px: dict[str, float]) -> None:
        eq = self.book.equity(px)
        row = (now, eq, *(self.agent_pnl[a]["all"] for a in AGENTS), px.get("BTC-USD", 0.0))
        self.equity_hist.append(row)
        if self.db is not None:
            self.db.add_equity([("team", now, eq), ("BTC", now, px.get("BTC-USD", 0.0))]
                               + [(a, now, self.agent_pnl[a]["all"]) for a in AGENTS])

    def drain(self) -> list[dict]:
        out, self.outbox = self.outbox, []
        return out

    def summary(self, now: float | None = None) -> dict:
        now = now or self.clock()
        px = self.px()
        eq = self.book.equity(px)
        d, m = et_day(now), et_month(now)
        day0 = self.day_start.get(d, eq)
        mon0 = self.month_start.get(m, eq)
        dt = datetime.fromtimestamp(now, ET)
        nxt = datetime(dt.year + (dt.month == 12), dt.month % 12 + 1, 1, tzinfo=ET)
        tax = self.book.tax_estimate(self.tax, px, time.gmtime(now).tm_year)
        exp = self.book.exposure(px)
        agents = []
        for a in AGENTS:
            ad = self.agent_day_start.get(d, {}).get(a, self.agent_pnl[a]["all"])
            am = self.agent_month_start.get(m, {}).get(a, self.agent_pnl[a]["all"])
            cap = max(eq * self.desk_w[a], 1e-9)
            gross = sum(abs(q) * px.get(k.split(":")[1], 0.0) for k, q in self.agent_q.get(a, {}).items()) / cap
            agents.append({"id": a, **AGENT_INFO[a], "weight": self.desk_w[a], "capital": eq * self.desk_w[a],
                           "mode": self.agent_mode[a], "detail": self.agent_detail[a], "invested": gross,
                           "pnl_today": self.agent_pnl[a]["all"] - ad, "pnl_mtd": self.agent_pnl[a]["all"] - am,
                           "pnl_all": self.agent_pnl[a]["all"], "fees": self.agent_pnl[a]["fees"],
                           "funding": self.agent_pnl[a]["funding"],
                           "targets": [{"key": k, "weight": v} for k, v in sorted(self.targets[a].items(), key=lambda x: -abs(x[1]))]})
        closed = list(self.oracle_closed)
        wins = sum(1 for t in closed if t["ret"] > 0)
        return {
            "ts": now,
            "account": {"equity": eq, "deposits": self.book.deposits, "cash": self.book.cash,
                        "pnl_today": eq - day0, "pnl_mtd": eq - mon0, "ret_mtd": eq / mon0 - 1 if mon0 else 0,
                        "pnl_all": eq - self.book.deposits, "ret_all": eq / self.book.deposits - 1,
                        "month": dt.strftime("%B %Y"), "days_left": (nxt - dt).days,
                        "exposure": exp, "fees": self.book.fees, "funding": self.book.funding_total,
                        "netting_saved": self.netting_saved, "margin": MARGIN * self.book.perp_gross(px),
                        "tax_ytd": tax, "fee_tier": self.fees.tier(now).name,
                        "costs": {"long_venue": self.costs.long_venue, "carry_on": self.costs.carry_on}},
            "agents": agents,
            "positions": self.positions_json(px),
            "oracle": {"open": self.oracle_trades, "closed": closed[:25], "wins": wins, "trades": len(closed)},
            "regime": self.regime,
            "nova": {"picks": self.nova_picks, "sharpe": self.nova_sharpe, "labels": STRAT_LABEL},
        }

    def positions_json(self, px: dict[str, float]) -> list[dict]:
        out = []
        eq = max(self.book.equity(px), 1e-9)
        owners = {}
        for a in AGENTS:
            for k, q in self.agent_q.get(a, {}).items():
                owners.setdefault(k, {})[a] = q
        for s in sorted(self.book.spot.lots):
            q = self.book.spot.qty(s)
            if q <= 0:
                continue
            basis = self.book.spot.basis(s)
            p = px.get(s, 0)
            out.append({"key": f"spot:{s}", "instrument": s, "venue": "spot", "side": "long", "qty": q,
                        "avg": basis / q, "mark": p, "value": q * p, "pnl": q * p - basis, "weight": q * p / eq,
                        "owners": owners.get(f"spot:{s}", {})})
        for s, pos in sorted(self.book.perps.items()):
            p = px.get(s, pos.avg)
            out.append({"key": f"perp:{s}", "instrument": perp_name(s), "product": f"{PERP_ID[s]}-20DEC30-CDE",
                        "venue": "perp", "side": "long" if pos.qty > 0 else "short",
                        "qty": pos.qty, "contracts": round(abs(pos.qty) / CONTRACT[s]), "avg": pos.avg, "mark": p,
                        "value": pos.qty * p, "pnl": pos.qty * (p - pos.avg), "funding": pos.funding,
                        "weight": pos.qty * p / eq, "owners": owners.get(f"perp:{s}", {})})
        return out

    # ============================================================ persistence
    def dump(self) -> dict:
        return {"book": self.book.dump(), "fees": self.fees.dump(), "desk_w": self.desk_w, "targets": self.targets,
                "fixed_q": self.fixed_q,
                "agent_q": self.agent_q, "agent_pnl": self.agent_pnl, "agent_mode": self.agent_mode,
                "agent_detail": self.agent_detail, "oracle_trades": self.oracle_trades,
                "oracle_closed": list(self.oracle_closed)[:100], "nova_picks": self.nova_picks,
                "regime": self.regime, "day_start": self.day_start, "month_start": self.month_start,
                "agent_day_start": self.agent_day_start, "agent_month_start": self.agent_month_start,
                "last_hour_bar": self.last_hour_bar, "last_day": self.last_day, "started": self.started,
                "carry_row": getattr(self, "_carry_row", {}), "nova_base": getattr(self, "_nova_base", {}),
                "nova_oracle": getattr(self, "_nova_oracle", {}), "fills": list(self.fills)[:150],
                "last_intent": self._last_intent, "prev_want": self._prev_want, "netting_saved": self.netting_saved,
                "tax": self.tax.to_json(), "balance": self.balance}

    def load(self, d: dict) -> None:
        self.book.load(d["book"])
        self.fees.load(d.get("fees", {}))
        self.desk_w = d["desk_w"]
        self.targets = d["targets"]
        self.fixed_q = d.get("fixed_q", self.fixed_q)
        self.agent_q = d.get("agent_q", self.agent_q)
        self.agent_pnl = d.get("agent_pnl", self.agent_pnl)
        self.agent_mode = d.get("agent_mode", self.agent_mode)
        self.agent_detail = d.get("agent_detail", self.agent_detail)
        self.oracle_trades = d.get("oracle_trades", [])
        self.oracle_closed = deque(d.get("oracle_closed", []), maxlen=200)
        self.nova_picks = d.get("nova_picks", {})
        self.regime = d.get("regime", {"btc_on": None})
        self.day_start = d.get("day_start", {})
        self.month_start = d.get("month_start", {})
        self.agent_day_start = d.get("agent_day_start", {})
        self.agent_month_start = d.get("agent_month_start", {})
        self.last_hour_bar = d.get("last_hour_bar", 0)
        self.last_day = d.get("last_day", 0)
        self.started = d.get("started", self.started)
        self._carry_row = d.get("carry_row", {})
        self._nova_base = d.get("nova_base", {})
        self._nova_oracle = d.get("nova_oracle", {})
        self.fills = deque(d.get("fills", []), maxlen=300)
        self._last_intent = {k: _freeze(v) for k, v in d.get("last_intent", {}).items()}
        self._prev_want = d.get("prev_want", {})
        self.netting_saved = d.get("netting_saved", 0.0)
        t = d.get("tax")
        if t:
            self.tax = TaxSettings(t.get("filing_status", "single"), t.get("other_income", 75_000),
                                   t.get("state", "XX"), t.get("state_rate_override"))
