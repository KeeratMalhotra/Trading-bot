"""Stream features: agent voices, weekly jerseys + earnings call, trade cards and milestones.

Display only: nothing here changes what the team trades. It reads the engine's real state
through hooks the engine calls, emits activity events, and is saved with the account.

  voices      each agent's latest line (voice.py), refreshed on its decisions and every few hours
  jerseys     weekly race between the agents (return on the capital each had at the week's start),
              a season table of weeks won, and an optional fan split set from a stream poll
  earnings    at each week's end (Monday 00:00 New York time): grades, winner, best/worst trade, desk
  cards       every closed trade: ORACLE's trades, and positions ATLAS/NOVA opened and fully closed
  milestones  new all-time closing highs, return marks, green-day streaks, trades closed, days on air
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from .book import perp_name
from .voice import line

ET = ZoneInfo("America/New_York")
AGENTS = ("ATLAS", "ORACLE", "NOVA")
IDLE_S = 3 * 3600          # an agent with nothing new to say gives a status line this often
STREAKS = (5, 7, 10, 14, 21, 30, 50, 100)
ATH_STEP = 0.03            # a new all-time high is announced when it's 3% above the last one announced
RETURNS = (0.05, 0.10, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0)
TRADES = (1, 10, 25, 50, 100, 250, 500, 1000, 2500)
DAYS_ON_AIR = (1, 7, 30, 50, 100, 180, 365, 500, 730, 1000)
GRADES = ((0.02, "A"), (0.005, "B"), (-0.005, "C"), (-0.02, "D"))   # weekly return on the agent's capital


def money(x: float, sign: bool = True) -> str:
    s = f"${abs(x):,.0f}" if abs(x) >= 100 else f"${abs(x):,.2f}"
    return (("+" if x >= 0 else "−") + s) if sign else (("−" if x < 0 else "") + s)


def pctf(x: float, d: int = 1) -> str:
    if round(abs(x) * 100, d) == 0:
        return f"{0:.{d}f}%"
    return f"{'+' if x >= 0 else '−'}{abs(x) * 100:.{d}f}%"


def rmul(x: float) -> str:
    return f"{'+' if x >= 0 else '−'}{abs(x):.1f}R"


def held(sec: float | None) -> str:
    if sec is None:
        return "—"
    d, h = int(sec // 86400), int(sec % 86400 // 3600)
    if d >= 10:
        return f"{d} days"
    return f"{d}d {h}h" if d else f"{h}h {int(sec % 3600 // 60)}m" if h else f"{max(int(sec // 60), 1)}m"


def pxf(p: float) -> str:
    if p >= 1000:
        return f"{p:,.0f}"
    if p >= 10:
        return f"{p:,.2f}"
    return f"{p:.4f}" if p >= 1 else f"{p:.5f}"


def grade(r: float) -> str:
    for thr, g in GRADES:
        if r >= thr:
            return g
    return "F"


def coin(s: str) -> str:
    return s.split("-")[0]


def et_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%Y-%m-%d")


class Show:
    def __init__(self, q):
        self.q = q
        self.rng = random.Random()
        self.voice: dict[str, dict | None] = {a: None for a in AGENTS}
        self.week: dict | None = None
        self.weeks: list[dict] = []          # finished weeks (compact), for the season table
        self.reports: list[dict] = []        # weekly earnings calls, newest first
        self.week_cards: list[dict] = []     # trades closed this week
        self.week_desk: list[str] = []       # desk allocation changes this week
        self.trades = 0
        self.ms: dict = {"ath": None, "streak": 0, "done": []}
        self.lives: dict[str, dict] = {}     # open perp positions: when/why they opened, running P&L
        self.day: str | None = None
        self.status: dict[str, dict] = {}    # facts for status lines, refreshed at each daily close
        self._tried: dict[str, float] = {}

    # ================================================================ voices
    def say(self, agent: str, key: str, now: float, **kw) -> None:
        text = line(agent, key, self.rng, **kw)
        if text and (self.voice.get(agent) or {}).get("text") != text:
            self.voice[agent] = {"text": text, "ts": now, "key": key}

    def _idle(self, a: str, now: float, px: dict[str, float]) -> None:
        """A status line from the current state (works right after a restart, before the next daily close)."""
        q, st = self.q, self.status.get(a) or {}
        if a == "ATLAS":
            reg = q.regime or {}
            if reg.get("btc_on") is None:
                return
            close, sma = reg.get("btc_close") or 0.0, reg.get("btc_sma200") or 0.0
            gap = f"{abs(close / sma - 1) * 100:.0f}%" if sma else "some way"
            days, shorts = st.get("days"), st.get("shorts", 0)
            if reg["btc_on"]:
                self.say(a, "bull_hold" if days else "bull_hold_nodays", now, days=days, gap=gap)
            elif shorts:
                self.say(a, "off_trades", now, n=shorts, s="s" if shorts != 1 else "", gap=gap)
            else:
                self.say(a, "off_cash" if days else "off_cash_nodays", now, days=days, gap=gap, sma=pxf(sma))
        elif a == "ORACLE" and not q.oracle_trades:
            fc, on = q.fc, q.regime.get("btc_on")
            if fc is None or not fc.ready or on is None:
                self.say(a, "warming", now)
                return
            bar = fc.latest_bar(now)
            side = "long" if on else "short"
            thr = fc.threshold(side, bar) if bar else float("nan")
            best = None
            for s in q.symbols:
                f = fc.forecast(s, bar) if bar else None
                if f and f.get(side) is not None and (best is None or f[side] > best[1]):
                    best = (s, f[side])
            if best and np.isfinite(thr):
                self.say(a, "scan_below", now, coin=coin(best[0]), best=f"{best[1]:+.2f}R", thr=f"{thr:+.2f}R",
                         n=len(q.symbols))
            else:
                self.say(a, "idle_none", now, n=len(q.symbols))
        elif a == "ORACLE":
            prog = []
            for t in q.oracle_trades:
                p = px.get(t["symbol"])
                if p and t["target"] != t["entry"]:
                    prog.append(((p - t["entry"]) / (t["target"] - t["entry"]), t))
            if prog:
                n = len(q.oracle_trades)
                best = max(prog, key=lambda x: x[0])
                if best[0] > 0:
                    self.say(a, "watching", now, n=n, s="s" if n != 1 else "", coin=coin(best[1]["symbol"]),
                             prog=f"{min(best[0], 1) * 100:.0f}%")
                else:
                    worst = min(prog, key=lambda x: x[0])
                    self.say(a, "watching_red", now, n=n, s="s" if n != 1 else "", coin=coin(worst[1]["symbol"]))
        elif a == "NOVA" and q.regime.get("btc_on") is not None:
            from .engine import STRAT_LABEL
            picks = ", ".join(f"{STRAT_LABEL.get(k, k)} {v * 100:.0f}%" for k, v in
                              sorted(q.nova_picks.items(), key=lambda x: -x[1]))
            d = st.get("review_in")
            kw = {"picks": picks, "days": d, "s": "s" if d != 1 else ""}
            if picks:
                self.say(a, "status" if d else "status_nodays", now, **kw)
            else:
                self.say(a, "status_cash" if d else "status_cash_nodays", now, **kw)

    # ================================================================ engine hooks
    def on_step(self, now: float, px: dict[str, float]) -> None:
        eq = self.q.book.equity(px)
        self._week(now, eq, px)
        d = et_day(now)
        if self.day is None:
            self.day = d
        elif d != self.day:
            self._day_closed(self.day, d, now, eq)
            self.day = d
        for a in AGENTS:
            v = self.voice.get(a)
            stale = v is None or now - v["ts"] >= (60 if v.get("key") == "warming" else IDLE_S)
            if stale and now - self._tried.get(a, 0) >= (60 if v is None or v.get("key") == "warming" else 600):
                self._tried[a] = now
                self._idle(a, now, px)

    def regime(self, bull: bool, close: float, sma: float, now: float) -> None:
        self.say("ATLAS", "regime_bull" if bull else "regime_off", now, close=pxf(close), sma=pxf(sma))

    def nova_review(self, picks: str, dropped: str, now: float) -> None:
        if not picks:
            self.say("NOVA", "review_cash", now)
        elif dropped:
            self.say("NOVA", "review_drop", now, picks=picks, dropped=dropped)
        else:
            self.say("NOVA", "review", now, picks=picks)

    def desk(self, old: dict[str, float], new: dict[str, float], now: float) -> None:
        moves = {a: new[a] - old.get(a, new[a]) for a in AGENTS}
        self.week_desk.append(" · ".join(f"{a} {old.get(a, 0) * 100:.0f}% → {new[a] * 100:.0f}%" for a in AGENTS))
        up, down = max(AGENTS, key=lambda a: moves[a]), min(AGENTS, key=lambda a: moves[a])
        if moves[up] > 0.01:
            self.say(up, "desk_up", now, w=f"{new[up] * 100:.0f}%")
        if moves[down] < -0.01:
            self.say(down, "desk_down", now, w=f"{new[down] * 100:.0f}%")

    def daily(self, st, i: int, now: float, regime_changed: bool, reviewed: bool, nova_picks: str,
              atlas_shorts: int) -> None:
        """Facts for the status lines, then each agent's daily line (unless it just said something bigger)."""
        q = self.q
        on = np.asarray(st.btc_on[:i + 1], bool)
        flips = np.flatnonzero(on != on[i])
        k = int(flips[-1]) + 1 if len(flips) else 0          # first day of the current regime
        self.status["ATLAS"] = {"days": i - k + 1, "shorts": atlas_shorts}
        r = (7 - (i + 1) % 7) % 7                     # NOVA reviews at the close of every 7th day
        self.status["NOVA"] = {"review_in": r or 7}
        px = q.px()
        if not regime_changed:
            self._idle("ATLAS", now, px)
        if not reviewed:
            self._idle("NOVA", now, px)

    def oracle_open(self, tr: dict, now: float) -> None:
        s, side = tr["symbol"], tr["side"]
        self.say("ORACLE", "open", now, Side=side.capitalize(), side=side, coin=coin(s), pred=f"{tr['forecast']:+.2f}R",
                 thr=f"{tr['thr']:+.2f}R", stop=pxf(tr["stop"]), target=pxf(tr["target"]))
        if tr.get("nova_qty"):
            cap = max(self.q.equity() * self.q.desk_w["NOVA"], 1e-9)
            self.say("NOVA", "copy", now, coin=coin(s), side=side, share=f"{abs(tr['nova_qty']) * tr['entry'] / cap * 100:.0f}%")

    def oracle_scan(self, coin_: str, best: float, thr: float, n_open: int, full: bool, n_coins: int, now: float) -> None:
        self.say("ORACLE", "scan_full" if full else "scan_below", now, coin=coin_, best=f"{best:+.2f}R",
                 thr=f"{thr:+.2f}R", n=n_coins, open=n_open)

    def oracle_close(self, tr: dict, now: float) -> None:
        s, side = tr["symbol"], tr["side"]
        move = tr["exit"] - tr["entry"]
        pnl, nova = tr["qty"] * move, tr.get("nova_qty", 0.0) * move
        self._card({"agent": "ORACLE", "agents": ["ORACLE"] + (["NOVA"] if tr.get("nova_qty") else []), "symbol": s,
                    "instrument": perp_name(s), "side": side, "entry": tr["entry"], "exit": tr["exit"],
                    "opened": tr["opened"], "closed": now, "pnl": pnl, "nova_pnl": nova, "ret": tr["ret"],
                    "R": tr["R"], "why": tr["why"]}, now)
        self.say("ORACLE", {"TARGET": "target", "STOP": "stop"}.get(tr["why"], "time"), now, coin=coin(s), side=side,
                 R=rmul(tr["R"]), ret=pctf(tr["ret"]), held=held(now - tr["opened"]))
        if abs(nova) >= 1:
            self.say("NOVA", "copy_win" if nova > 0 else "copy_loss", now, coin=coin(s), side=side, pnl=money(nova))

    def perp_fill(self, s: str, q0: float, avg0: float, fund0: float, fill, now: float) -> None:
        """Track each perp position from open to full close; ATLAS/NOVA positions get a trade card when
        they close (ORACLE's own trades get theirs from oracle_close)."""
        q = self.q
        k = f"perp:{s}"
        pos = q.book.perps.get(s)
        q1 = pos.qty if pos else 0.0
        owners = {a for a in ("ATLAS", "NOVA") if k in q.targets.get(a, {})}
        life = self.lives.get(s)
        if abs(q0) < 1e-12:
            self.lives[s] = {"opened": now, "side": "long" if q1 > 0 else "short", "realized": 0.0, "fees": fill.fee,
                             "fund0": 0.0, "owners": sorted(owners)}
            return
        if life is None:                          # opened before this was tracked (older saved account)
            life = self.lives[s] = {"opened": None, "side": "long" if q0 > 0 else "short", "realized": 0.0,
                                    "fees": 0.0, "fund0": fund0, "owners": [], "untracked": True}
        life["realized"] += fill.realized
        life["fees"] += fill.fee
        life["owners"] = sorted(set(life["owners"]) | owners)
        if abs(q1) > 1e-12 and (q1 > 0) == (q0 > 0):
            return
        pnl = life["realized"] - life["fees"] - (fund0 - life["fund0"])
        if life["owners"] and not life.get("untracked"):
            agent = "ATLAS" if "ATLAS" in life["owners"] else life["owners"][0]
            sg = 1 if life["side"] == "long" else -1
            self._card({"agent": agent, "agents": life["owners"], "symbol": s, "instrument": perp_name(s),
                        "side": life["side"], "entry": avg0, "exit": fill.price, "opened": life["opened"], "closed": now,
                        "pnl": pnl, "ret": sg * (fill.price / avg0 - 1) if avg0 else 0.0, "why": "PLAN"}, now)
            for a in life["owners"]:
                self.say(a, "card_win" if pnl >= 0 else "card_loss", now, inst=perp_name(s), side=life["side"],
                         pnl=money(pnl), held=held(now - life["opened"]))
        del self.lives[s]
        if abs(q1) > 1e-12:                       # flipped long <-> short: a new position starts
            self.lives[s] = {"opened": now, "side": "long" if q1 > 0 else "short", "realized": 0.0, "fees": 0.0,
                             "fund0": pos.funding, "owners": sorted(owners)}

    # ================================================================ cards & milestones
    def _card(self, card: dict, now: float) -> None:
        self.trades += 1
        card["n"] = self.trades
        self.week_cards.append({k: card[k] for k in ("agent", "instrument", "side", "pnl", "ret")})
        self.week_cards = self.week_cards[-300:]
        verb = {"TARGET": " · target hit", "STOP": " · stopped out", "TIME": " · 14-day window ended"}.get(card["why"], "")
        self.q.emit("card", card["agent"], f"{card['agent']} closed {card['instrument']} {card['side']}{verb}",
                    f"{pxf(card['entry'])} → {pxf(card['exit'])} · {money(card['pnl'])} · held {held(now - card['opened']) if card['opened'] else '—'}",
                    "good" if card["pnl"] >= 0 else "bad", card["symbol"], data={"card": card})
        for n in TRADES:
            if self.trades == n:
                self._milestone(f"trades-{n}", "trades", "First trade closed" if n == 1 else f"{n} trades closed",
                                f"Trade #{n}: {card['agent']} {card['instrument']} {card['side']}, {money(card['pnl'])}.")

    def _milestone(self, key: str | None, kind: str, title: str, text: str) -> None:
        if key is not None:
            if key in self.ms["done"]:
                return
            self.ms["done"].append(key)
        self.q.emit("milestone", "DESK", title, text, "good",
                    data={"milestone": {"kind": kind, "title": title, "text": text}})

    def _day_closed(self, prev: str, today: str, now: float, eq: float) -> None:
        q, ms = self.q, self.ms
        start, close = q.day_start.get(prev), q.day_start.get(today, eq)
        if start is None:
            return
        dep = max(q.book.deposits, 1e-9)
        ms["streak"] = ms["streak"] + 1 if close - start > 0.01 else 0
        if ms["streak"] in STREAKS:
            self._milestone(None, "streak", f"{ms['streak']} green days in a row",
                            f"The account closed higher {ms['streak']} days running. Yesterday {money(close - start)}.")
        ath = max(ms["ath"] or dep, dep)
        if close >= ath * (1 + ATH_STEP):          # always a true all-time high: nothing between reached this
            ms["ath"] = close
            self._milestone(None, "ath", f"New all-time high: {money(close, False)}",
                            f"Best close since the start, {pctf(close / dep - 1)}.")
        for r in RETURNS:
            if close / dep - 1 >= r:
                self._milestone(f"ret-{r}", "return", f"Up {r * 100:.0f}% since the start",
                                f"The account closed at {money(close, False)} on a {money(dep, False)} start.")
        days = int((now - q.started) // 86400)
        for n in DAYS_ON_AIR:
            if days >= n:
                self._milestone(f"days-{n}", "days", f"{n} day{'s' if n != 1 else ''} on air",
                                f"Trading live prices since {datetime.fromtimestamp(q.started, ET):%b} "
                                f"{datetime.fromtimestamp(q.started, ET).day}, {datetime.fromtimestamp(q.started, ET).year}.")

    # ================================================================ jerseys & earnings call
    @staticmethod
    def _week_of(now: float) -> tuple[str, date]:
        dt = datetime.fromtimestamp(now, ET)
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}", (dt - timedelta(days=dt.weekday())).date()

    def _week(self, now: float, eq: float, px: dict[str, float]) -> None:
        wid, monday = self._week_of(now)
        if self.week is not None and self.week["id"] == wid:
            return
        if self.week is not None:
            self._close_week(now, eq, px)
        q = self.q
        self.week = {"id": wid, "monday": monday.isoformat(), "t0": now, "eq0": eq, "btc0": px.get("BTC-USD"),
                     "pnl0": {a: q.agent_pnl[a]["all"] for a in AGENTS},
                     "cap0": {a: eq * q.desk_w[a] for a in AGENTS}, "fans": None}
        self.week_cards, self.week_desk = [], []

    def standings(self) -> dict[str, dict]:
        wk, q = self.week, self.q
        if wk is None:
            return {}
        out = {a: {"pnl": q.agent_pnl[a]["all"] - wk["pnl0"][a]} for a in AGENTS}
        for a in AGENTS:
            out[a]["ret"] = out[a]["pnl"] / max(wk["cap0"][a], 1e-9)
        for i, a in enumerate(sorted(AGENTS, key=lambda a: -out[a]["ret"])):
            out[a]["rank"] = i + 1
        return out

    def season(self) -> dict[str, int]:
        return {a: sum(1 for w in self.weeks if w["winner"] == a) for a in AGENTS}

    @staticmethod
    def week_label(wk: dict, end: float | None = None) -> str:
        """Monday – Sunday of the week (from the account's first day, for the first week)."""
        monday = date.fromisoformat(wk["monday"])
        b = monday + timedelta(days=6)
        a = min(max(monday, datetime.fromtimestamp(wk["t0"], ET).date()), b)
        f = lambda d: f"{d:%b} {d.day}"  # noqa: E731
        return f"{f(a)} – {f(b)}" if a != b else f(a)

    def set_fans(self, counts: dict[str, float]) -> dict | None:
        if self.week is None:
            return None
        tot = sum(max(float(counts.get(a, 0) or 0), 0) for a in AGENTS)
        self.week["fans"] = {a: max(float(counts.get(a, 0) or 0), 0) / tot for a in AGENTS} if tot > 0 else None
        if self.week["fans"]:
            self.q.emit("note", "DESK", "Fans picked their jerseys",
                        " · ".join(f"{a} {self.week['fans'][a] * 100:.0f}%" for a in AGENTS), "info")
        return self.week["fans"]

    def _close_week(self, now: float, eq: float, px: dict[str, float]) -> None:
        wk = self.week
        st = self.standings()
        winner = min(AGENTS, key=lambda a: st[a]["rank"])
        btc = px.get("BTC-USD")
        btc_ret = btc / wk["btc0"] - 1 if btc and wk.get("btc0") else None
        cards = sorted(self.week_cards, key=lambda c: c["pnl"])
        self.weeks = (self.weeks + [{"id": wk["id"], "winner": winner, "ret": {a: st[a]["ret"] for a in AGENTS}}])[-260:]
        acct = {"pnl": eq - wk["eq0"], "ret": eq / wk["eq0"] - 1 if wk["eq0"] else 0.0}
        rep = {"id": wk["id"], "label": self.week_label(wk, now), "from": wk["t0"], "to": now, "account": acct,
               "btc_ret": btc_ret, "winner": winner, "trades": len(self.week_cards),
               "best": cards[-1] if cards and cards[-1]["pnl"] > 0 else None,
               "worst": cards[0] if cards and cards[0]["pnl"] < 0 else None,
               "desk": list(self.week_desk), "fans": wk.get("fans"), "wins": self.season(),
               "agents": {a: {**st[a], "grade": grade(st[a]["ret"])} for a in AGENTS}}
        for a in AGENTS:
            r, ret = st[a]["rank"], st[a]["ret"]
            if abs(ret) < 0.0005:
                key = "week_flat"
            elif r == 1:
                key = "week_first" if ret > 0 else "week_first_red"
            elif r == len(AGENTS):
                key = "week_last_green" if ret > 0 else "week_last"
            else:
                key = "week_mid"
            self.say(a, key, now, ret=pctf(ret))
            rep["agents"][a]["line"] = (self.voice.get(a) or {}).get("text")
        fans = wk.get("fans")
        if fans:
            fav = max(AGENTS, key=lambda a: fans[a])
            rep["fans_result"] = (f"{fans[winner] * 100:.0f}% of fans backed the winner"
                                  + ("." if fav == winner else f"; most backed {fav}."))
        self.reports = ([rep] + self.reports)[:12]
        best = rep["best"]
        self.q.emit("weekly", "DESK", f"Weekly earnings call · {rep['label']}",
                    f"{winner} wins the week ({pctf(st[winner]['ret'])}). Account {money(acct['pnl'])} ({pctf(acct['ret'])})"
                    + (f" vs BTC {pctf(btc_ret)}" if btc_ret is not None else "") + ". "
                    + (f"Best trade: {best['agent']} {best['instrument']} {best['side']} {money(best['pnl'])}."
                       if best else "No winning trades closed this week."),
                    "good" if acct["pnl"] >= 0 else "bad", data={"report": rep})

    # ================================================================ output / persistence
    def summary(self, now: float) -> dict:
        wk = self.week
        return {"week": None if wk is None else {"id": wk["id"], "label": self.week_label(wk, now), "fans": wk.get("fans")},
                "standings": self.standings(), "wins": self.season(), "reports": self.reports[:4]}

    def dump(self) -> dict:
        return {"voice": self.voice, "week": self.week, "weeks": self.weeks, "reports": self.reports,
                "week_cards": self.week_cards, "week_desk": self.week_desk, "trades": self.trades, "ms": self.ms,
                "lives": self.lives, "day": self.day, "status": self.status}

    def catch_up(self, eq: float, trades_so_far: int) -> None:
        """First run on an account that existed before these features: mark what it already achieved as
        done (days on air, return marks, trades) so the stream doesn't get a burst of old milestones."""
        q = self.q
        dep = max(q.book.deposits, 1e-9)
        days = int((q.clock() - q.started) // 86400)
        self.trades = trades_so_far
        done = set(self.ms["done"])
        done |= {f"days-{n}" for n in DAYS_ON_AIR if days >= n}
        done |= {f"ret-{r}" for r in RETURNS if eq / dep - 1 >= r}
        done |= {f"trades-{n}" for n in TRADES if trades_so_far >= n}
        self.ms["done"] = sorted(done)
        self.ms["ath"] = max(eq, dep)

    def load(self, d: dict) -> None:
        self.voice = {a: (d.get("voice") or {}).get(a) for a in AGENTS}
        self.week = d.get("week")
        self.weeks = d.get("weeks", [])
        self.reports = d.get("reports", [])
        self.week_cards = d.get("week_cards", [])
        self.week_desk = d.get("week_desk", [])
        self.trades = d.get("trades", 0)
        self.ms = {"ath": None, "streak": 0, "done": [], **(d.get("ms") or {})}
        self.lives = d.get("lives", {})
        self.day = d.get("day")
        self.status = d.get("status", {})
        for s, pos in self.q.book.perps.items():     # positions from before tracking: no card when they close
            if s not in self.lives and pos.qty:
                self.lives[s] = {"opened": None, "side": "long" if pos.qty > 0 else "short", "realized": 0.0,
                                 "fees": 0.0, "fund0": pos.funding, "owners": [], "untracked": True}
