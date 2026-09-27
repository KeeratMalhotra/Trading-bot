"""QUORUM research: a strategy library, three adaptive agents and a team desk, on daily data.

    python -m app.team.research

Everything decided at a day's close uses only data up to that close and earns the NEXT
day's return. Costs: US perpetual-style futures ~0.05% fee + slippage per side, spot legs
0.50% per side, and hourly funding (Deribit history as a proxy for Coinbase US perps).
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

from ..config import DATA_DIR
from ..ml.altdata import AltData
from ..ml.data import HistoryStore

DAY = 86400
START = 1640995200  # 2022-01-01
PERP_COST = {"BTC-USD": 0.0008, "ETH-USD": 0.0008}  # fee + slippage per side
PERP_COST_ALT = 0.0013
SPOT_COST = 0.0050


# ----------------------------------------------------------------- data
@dataclass
class Daily:
    days: np.ndarray          # (D,) day index (unix // 86400)
    symbols: list[str]
    C: np.ndarray             # close (D, S)
    H: np.ndarray
    L: np.ndarray
    R: np.ndarray             # close-to-close return (D, S), 0 where unknown
    F: np.ndarray             # funding paid by longs during the day (D, S)
    G: np.ndarray             # fear & greed (D,)
    avail: np.ndarray         # (D, S) bool


def load_daily(store: HistoryStore, alt: AltData) -> Daily:
    syms = store.symbols
    first = min(int(store.raw[s][0, 0]) for s in syms if len(store.raw[s])) // DAY
    # a day counts once its last hourly candle (23:00 UTC) exists; a partial day is excluded
    last = (int(store.raw["BTC-USD"][-1, 0]) + 3600) // DAY
    days = np.arange(first, last)
    D, S = len(days), len(syms)
    C = np.full((D, S), np.nan)
    H, L = C.copy(), C.copy()
    F = np.zeros((D, S))
    for j, s in enumerate(syms):
        arr = store.raw[s]
        d = (arr[:, 0] // DAY).astype(np.int64)
        u, first_i = np.unique(d, return_index=True)
        last_i = np.append(first_i[1:], len(d)) - 1
        ok = (u >= first) & (u < last)
        idx = u[ok] - first
        C[idx, j] = arr[last_i[ok], 4]
        H[idx, j] = np.maximum.reduceat(arr[:, 2], first_i)[ok]
        L[idx, j] = np.minimum.reduceat(arr[:, 3], first_i)[ok]
        # exchange outages: carry the last price across gaps of up to 3 days (real delistings stay gaps)
        gap = 0
        for i in range(1, D):
            if np.isfinite(C[i, j]):
                gap = 0
            elif np.isfinite(C[i - 1, j]) or gap:
                gap += 1
                if gap <= 3 and np.isfinite(C[i - gap, j]):
                    C[i, j] = H[i, j] = L[i, j] = C[i - gap, j]
        hours = (days[:, None] * DAY + np.arange(24)[None, :] * 3600).ravel()
        F[:, j] = alt.hourly_funding(s, hours).reshape(D, 24).sum(axis=1)
    R = np.zeros((D, S))
    R[1:] = np.where(np.isfinite(C[1:]) & np.isfinite(C[:-1]), C[1:] / C[:-1] - 1, 0.0)
    G = np.full(D, np.nan)
    if len(alt.fng):
        gi = np.searchsorted(alt.fng[:, 0] // DAY, days, side="right") - 1
        G[gi >= 0] = alt.fng[gi[gi >= 0], 1]
    return Daily(days, syms, C, H, L, R, F, G, np.isfinite(C))


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(x.shape, np.nan)
    for j in range(x.shape[1] if x.ndim == 2 else 1):
        col = x[:, j] if x.ndim == 2 else x
        ok = np.isfinite(col)
        v = np.where(ok, col, 0.0)
        cs = np.cumsum(np.insert(v, 0, 0.0))
        cn = np.cumsum(np.insert(ok.astype(float), 0, 0.0))
        m = np.full(len(col), np.nan)
        if len(col) >= n:
            s = (cs[n:] - cs[:-n])
            c = (cn[n:] - cn[:-n])
            m[n - 1:] = np.where(c == n, s / n, np.nan)
        if x.ndim == 2:
            out[:, j] = m
        else:
            out = m
    return out


def rolling_max(x, n):
    out = np.full(x.shape, np.nan)
    for i in range(n, len(x)):
        out[i] = np.max(x[i - n:i], axis=0)
    return out


def rolling_min(x, n):
    out = np.full(x.shape, np.nan)
    for i in range(n, len(x)):
        out[i] = np.min(x[i - n:i], axis=0)
    return out


# ----------------------------------------------------------- strategies
def regime(d: Daily) -> np.ndarray:
    """+1 bull, -1 bear, 0 chop (BTC close vs 50/200-day averages)."""
    b = d.symbols.index("BTC-USD")
    c = d.C[:, [b]]
    s50, s200 = sma(c, 50)[:, 0], sma(c, 200)[:, 0]
    c = c[:, 0]
    reg = np.zeros(len(c))
    reg[(c > s200) & (s50 > s200)] = 1
    reg[(c < s200) & (s50 < s200)] = -1
    reg[~np.isfinite(s200)] = 0
    return reg


def strat_trend(d: Daily) -> np.ndarray:
    """BTC & ETH each: long in its own uptrend, short in its own downtrend (perps)."""
    W = np.zeros(d.C.shape)
    s50, s200 = sma(d.C, 50), sma(d.C, 200)
    for s in ("BTC-USD", "ETH-USD"):
        j = d.symbols.index(s)
        up = (d.C[:, j] > s200[:, j]) & (s50[:, j] > s200[:, j])
        dn = (d.C[:, j] < s200[:, j]) & (s50[:, j] < s200[:, j])
        W[up, j] = 0.5
        W[dn, j] = -0.5
    return W


def strat_rotation(d: Daily, reg: np.ndarray, every: int = 7) -> np.ndarray:
    """Bull: long the 3 strongest coins (90d). Bear: short the 3 weakest. Weekly."""
    W = np.zeros(d.C.shape)
    s50 = sma(d.C, 50)
    mom = np.full(d.C.shape, np.nan)
    mom[90:] = d.C[90:] / d.C[:-90] - 1
    cur = np.zeros(d.C.shape[1])
    for i in range(len(d.days)):
        if i % every == 0 or np.sign(cur.sum()) != reg[i]:
            cur = np.zeros(d.C.shape[1])
            m = mom[i]
            if reg[i] > 0:
                ok = np.isfinite(m) & (m > 0) & (d.C[i] > s50[i])
                top = [j for j in np.argsort(-np.where(ok, m, -np.inf))[:3] if ok[j]]
                for j in top:
                    cur[j] = 1 / 3
            elif reg[i] < 0:
                ok = np.isfinite(m) & (m < 0) & (d.C[i] < s50[i])
                bot = [j for j in np.argsort(np.where(ok, m, np.inf))[:3] if ok[j]]
                for j in bot:
                    cur[j] = -1 / 6
        cur = np.where(d.avail[i], cur, 0.0)
        W[i] = cur
    return W


def strat_carry(d: Daily, enter=0.12, exit_=0.03, per=0.25, maxn=3) -> np.ndarray:
    """Delta-neutral: long spot + short perp while funding is rich. Returns hedged notional."""
    Cw = np.zeros(d.C.shape)
    f7 = np.full(d.F.shape, np.nan)
    f7[7:] = np.array([d.F[i - 7:i].mean(axis=0) for i in range(7, len(d.F))]) * 365
    held: set[int] = set()
    for i in range(len(d.days)):
        f = f7[i]
        for j in list(held):
            if not np.isfinite(f[j]) or f[j] < exit_ or not d.avail[i, j]:
                held.discard(j)
        cands = [j for j in np.argsort(-np.nan_to_num(f, nan=-9)) if np.isfinite(f[j]) and f[j] > enter and d.avail[i, j]]
        for j in cands:
            if len(held) >= maxn:
                break
            held.add(j)
        for j in held:
            Cw[i, j] = per
    return Cw


def strat_breakout(d: Daily, reg: np.ndarray) -> np.ndarray:
    """NOMAD-style Donchian, both directions, ATR-risk sized (1% risk / 2 ATR)."""
    W = np.zeros(d.C.shape)
    hi20, lo20 = rolling_max(d.H, 20), rolling_min(d.L, 20)
    hi10, lo10 = rolling_max(d.H, 10), rolling_min(d.L, 10)
    s50 = sma(d.C, 50)
    pc = np.vstack([d.C[:1], d.C[:-1]])
    tr = np.nanmax(np.stack([d.H - d.L, np.abs(d.H - pc), np.abs(d.L - pc)]), axis=0)
    atr = sma(tr, 20)
    state = np.zeros(d.C.shape[1])
    size = np.zeros(d.C.shape[1])
    for i in range(len(d.days)):
        c = d.C[i]
        for j in range(d.C.shape[1]):
            if not d.avail[i, j] or not np.isfinite(atr[i, j]):
                state[j] = 0
                continue
            if state[j] > 0 and (c[j] < lo10[i, j] or reg[i] <= 0):
                state[j] = 0
            elif state[j] < 0 and (c[j] > hi10[i, j] or reg[i] >= 0):
                state[j] = 0
            if state[j] == 0:
                sz = min(0.01 / (2 * atr[i, j] / c[j]), 0.25)
                if reg[i] > 0 and c[j] > hi20[i, j] and c[j] > s50[i, j]:
                    state[j], size[j] = 1, sz
                elif reg[i] < 0 and c[j] < lo20[i, j] and c[j] < s50[i, j]:
                    state[j], size[j] = -1, sz
        W[i] = state * size
        g = np.abs(W[i]).sum()
        if g > 1:
            W[i] /= g
    return W


NOVA_UNIVERSE = ["btc_regime", "trend", "rotation", "breakout", "carry", "oracle_long", "oracle_short"]


def strat_hodl(d: Daily) -> np.ndarray:
    W = np.zeros(d.C.shape)
    W[:, d.symbols.index("BTC-USD")] = 1.0
    return W


def sleeve_returns(d: Daily, W: np.ndarray, Cw: np.ndarray | None = None, spot: bool = False,
                   spot_cost: float = SPOT_COST) -> np.ndarray:
    """Daily net returns of a weight matrix decided at close i, earning day i+1."""
    cost = np.array([PERP_COST.get(s, PERP_COST_ALT) for s in d.symbols])
    out = np.zeros(len(d.days))
    prev = np.zeros(d.C.shape[1])
    prevc = np.zeros(d.C.shape[1])
    for i in range(1, len(d.days)):
        w = W[i - 1]
        r = (w * d.R[i]).sum()
        if not spot:
            r -= (w * d.F[i]).sum()                # longs pay positive funding, shorts receive it
        turn = np.abs(w - prev)
        r -= (turn * (spot_cost if spot else cost)).sum()
        if Cw is not None:
            c = Cw[i - 1]
            r += (c * d.F[i]).sum()                # short perp leg receives funding
            r -= (np.abs(c - prevc) * (spot_cost + cost)).sum()
            prevc = c
        prev = w
        out[i] = r
    return out


# ------------------------------------------------------------- ORACLE
def load_oos(side: str) -> np.ndarray:
    oos = np.load(DATA_DIR / "research" / ("oracle_oos_Dr.npz" if side == "long" else "oracle_oos_Sr.npz"))
    return oos["P"].astype(float)


def oracle_thresholds(P: np.ndarray) -> np.ndarray:
    from ..ml.oracle import THRESHOLD_LOOKBACK, THRESHOLD_Q
    T = len(P)
    thr = np.full(T, np.inf)
    for i in range(THRESHOLD_LOOKBACK, T, 24):
        w = P[i - THRESHOLD_LOOKBACK:i].ravel()
        w = w[np.isfinite(w)]
        if len(w) > 500:
            thr[i:i + 24] = np.quantile(w, THRESHOLD_Q)
    return thr


def daily_regime(d: Daily) -> np.ndarray:
    """THE bull filter used by every agent: BTC's daily close above its 200-day average."""
    b = d.symbols.index("BTC-USD")
    return d.C[:, b] > sma(d.C[:, [b]], 200)[:, 0]


def regime_at(d: Daily, on: np.ndarray, ts: np.ndarray) -> np.ndarray:
    """Regime in force at times `ts`: that of the last day completed at or before ts."""
    k = np.searchsorted((d.days + 1) * DAY, ts, side="right") - 1
    out = np.zeros(len(ts), dtype=bool)
    ok = k >= 0
    out[ok] = on[k[ok]]
    return out


def oracle_sleeve(d: Daily, store: HistoryStore, alt: AltData, per: float = 0.2, maxn: int = 5,
                  side: str = "long", P: np.ndarray | None = None, panel=None, start: float = START - 30 * DAY):
    """Replays ORACLE trades (top-10% forecasts) on perps, marked daily.
    long: only while BTC > 200-day avg; short: only while BTC < 200-day avg.
    Returns (daily returns, daily average exposure)."""
    from ..ml.dataset import build_panel
    if P is None:
        P = load_oos(side)
    if panel is None:
        panel = build_panel(store, tp_mult=4, sl_mult=2, horizon=336, side=side)
    P = P[:len(panel.t)]
    if len(P) < len(panel.t):  # forecasts not yet made for the newest hours
        P = np.vstack([P, np.full((len(panel.t) - len(P), P.shape[1]), np.nan)])
    sgn = 1.0 if side == "long" else -1.0
    T, S = P.shape
    thr = oracle_thresholds(P)
    on = regime_at(d, daily_regime(d), panel.t + 3600)
    if side == "short":
        on = ~on
    hourly = np.zeros(T)
    expo = np.zeros(T)
    busy = np.full(S, -1)
    open_until: list[int] = []
    cost = np.array([PERP_COST.get(s, PERP_COST_ALT) for s in panel.symbols])
    from .book import STOP_SLIP, STOP_SLIP_ALT
    stop_slip = np.array([STOP_SLIP.get(s, STOP_SLIP_ALT) for s in panel.symbols])
    close = panel.close
    hours_ts = panel.t
    fund = np.stack([alt.hourly_funding(s, hours_ts) for s in panel.symbols], axis=1)
    for i in range(T):
        if hours_ts[i] < start:
            continue
        open_until = [u for u in open_until if u > i]
        if not on[i] or not np.isfinite(thr[i]):
            continue
        cands = sorted(((P[i, j], j) for j in range(S) if np.isfinite(P[i, j]) and P[i, j] >= thr[i]
                        and busy[j] < i and np.isfinite(panel.dvol[i, j])), reverse=True)
        for p, j in cands:
            if len(open_until) >= maxn:
                break
            if np.isfinite(panel.ret[i, j]):             # outcome known (trade finished inside the data)
                h = int(panel.held[i, j])
                r = panel.ret[i, j]
                done = True
            else:                                         # trade still open at the end of the data
                h = T - 1 - i
                r = np.nan
                done = False
            if h <= 0:
                continue
            path = close[i + 1:i + h + 1, j].copy()
            if done:
                exit_px = close[i, j] * (1 + r) if side == "long" else close[i, j] / (1 + r)
                path[-1] = exit_px
            prev = close[i, j]
            hourly[i + 1] -= per * cost[j]
            for k, px in enumerate(path):
                if not np.isfinite(px):
                    break
                hourly[i + 1 + k] += sgn * per * (px - prev) / close[i, j] - sgn * per * fund[i + 1 + k, j] * (prev / close[i, j])
                expo[i + 1 + k] += per
                prev = px
            if done:
                hourly[i + h] -= per * cost[j] * (path[-1] / close[i, j])
                if r <= -panel.sl_mult * panel.dvol[i, j] * 0.999:  # stopped out: fast-market slippage
                    hourly[i + h] -= per * stop_slip[j]
            busy[j] = i + h
            open_until.append(i + h if done else T + 10**6)
    # hourly -> daily (simple sum of per-capital returns within the day)
    day_of = (hours_ts // DAY).astype(np.int64)
    out = np.zeros(len(d.days))
    idx = np.searchsorted(d.days, day_of)
    ok = (idx < len(d.days)) & (d.days[np.clip(idx, 0, len(d.days) - 1)] == day_of)
    np.add.at(out, idx[ok], hourly[ok])
    ex = np.zeros(len(d.days))
    cnt = np.zeros(len(d.days))
    np.add.at(ex, idx[ok], expo[ok])
    np.add.at(cnt, idx[ok], 1)
    return out, np.clip(ex / np.maximum(cnt, 1), 0, 1)


def strat_btc_regime(d: Daily) -> np.ndarray:
    """All-in BTC while BTC closes above its 200-day average, cash otherwise (via US perps:
    ~0.08% per side instead of 0.5%+ on spot for a small account, pays funding)."""
    W = np.zeros(d.C.shape)
    W[daily_regime(d), d.symbols.index("BTC-USD")] = 1.0
    return W


# ---------------------------------------------------------------- agents
def sharpe(r: np.ndarray) -> float:
    s = r.std()
    return float(r.mean() / s * np.sqrt(365)) if s > 0 else 0.0


def agent_atlas(d, lib, btc_on) -> np.ndarray:
    """Regime strategist. Bull (BTC > 200d avg): ride BTC. Otherwise: half breakout (shorts the
    breakdowns in bear markets), half delta-neutral carry."""
    out = np.zeros(len(d.days))
    for i in range(1, len(d.days)):
        if btc_on[i - 1]:
            out[i] = lib["btc_regime"][i]
        else:
            out[i] = 0.5 * lib["breakout"][i] + 0.5 * lib["carry"][i]
    # the switch itself (btc_regime sleeve already pays its spot fees)
    return out


def agent_nova(d, lib, names, lookback=90, every=7, switch_cost=0.002) -> tuple[np.ndarray, list, np.ndarray]:
    """Adaptive learner: shadow-tracks EVERY strategy (its teammates' too). Once a week it holds
    all strategies whose last-90-day Sharpe is positive, weighted by inverse volatility
    (risk parity), and sits in cash when nothing is working.
    hist[i] = weights decided at the close of day i (held on day i+1)."""
    R = np.stack([lib[n] for n in names], axis=1)
    D = len(d.days)
    out = np.zeros(D)
    w = np.zeros(len(names))
    hist = np.zeros((D, len(names)))
    log = []
    for i in range(D):
        if i >= 1:
            out[i] += (hist[i - 1] * R[i]).sum()
        if (i + 1) % every == 0 and i + 1 > lookback:
            win = R[i + 1 - lookback:i + 1]
            sh = np.array([sharpe(win[:, k]) for k in range(len(names))])
            vol = win.std(axis=0) + 1e-9
            picks = [k for k in range(len(names)) if sh[k] > 0]
            new = np.zeros(len(names))
            if picks:
                inv = np.array([1 / vol[k] for k in picks])
                for k, x in zip(picks, inv / inv.sum(), strict=True):
                    new[k] = x
            if i + 1 < D:
                out[i + 1] -= np.abs(new - w).sum() * switch_cost
            if not np.allclose(new, w):
                log.append((int(d.days[i]), [names[k] for k in picks]))
            w = new
        hist[i] = w
    return out, log, hist


def team(d, agents: dict[str, np.ndarray], lookback=90):
    """The desk: at every month end it re-allocates capital between the agents by their
    last-90-day Sharpe (each keeps 20%-60%). W[i] = weights decided at the close of day i."""
    names = list(agents)
    A = np.stack([agents[n] for n in names], axis=1)
    D = len(d.days)
    w = np.full(len(names), 1 / len(names))
    out = np.zeros(D)
    W = np.zeros((D, len(names)))
    for i in range(D):
        if i >= 1:
            out[i] = (W[i - 1] * A[i]).sum()
        this_m = time.gmtime(int(d.days[i]) * DAY).tm_mon
        next_m = time.gmtime(int(d.days[i] + 1) * DAY).tm_mon
        if next_m != this_m and i + 1 > lookback:
            sh = np.array([sharpe(A[i + 1 - lookback:i + 1, k]) for k in range(len(names))])
            raw = np.maximum(sh, 0) + 0.5
            w = np.clip(raw / raw.sum(), 0.2, 0.6)
            w /= w.sum()
        W[i] = w
    return out, W, names


# ---------------------------------------------------------------- report
def stats(d, r, lo=START):
    m = d.days * DAY >= lo
    r = r[m]
    days = d.days[m]
    eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    months = np.array([time.strftime("%Y-%m", time.gmtime(int(x) * DAY)) for x in days])
    mr = []
    for mm in sorted(set(months)):
        mr.append(np.prod(1 + r[months == mm]) - 1)
    mr = np.array(mr)
    years = {}
    for y in range(2022, 2027):
        k = np.array([mm.startswith(str(y)) for mm in sorted(set(months))])
        if k.any():
            years[y] = round((np.prod(1 + mr[k]) - 1) * 100, 1)
    return {"total_pct": round((eq[-1] - 1) * 100, 1), "max_dd_pct": round(float((1 - eq / peak).max()) * 100, 1),
            "sharpe": round(sharpe(r), 2), "months": len(mr), "up_months": int((mr > 0.0005).sum()),
            "down_months": int((mr < -0.0005).sum()), "worst_month_pct": round(float(mr.min()) * 100, 1),
            "best_month_pct": round(float(mr.max()) * 100, 1), "yearly_pct": years}


@dataclass
class DeskState:
    d: Daily
    lib: dict[str, np.ndarray]           # daily returns of every strategy
    targets: dict[str, np.ndarray]       # weight matrices (D, S): strategy -> perp/spot weights
    carry_w: np.ndarray                  # (D, S) hedged notional (long spot + short perp)
    btc_on: np.ndarray                   # (D,) bull filter
    agents: dict[str, np.ndarray]        # daily returns per agent
    nova_w: np.ndarray                   # (D, K) NOVA weights over NOVA_UNIVERSE
    desk_w: np.ndarray                   # (D, 3) desk weights ATLAS/ORACLE/NOVA
    team: np.ndarray                     # (D,) team returns
    oracle_exposure: np.ndarray          # (D,) share of ORACLE capital in trades


@dataclass(frozen=True)
class CostModel:
    """Execution desk: which venue each leg uses, given the account's spot fee tier.
    Small accounts pay ~0.5-1.2% on spot, so directional longs go through US perps (~0.08%)
    and the spot-legged carry trade only runs when spot fees are low enough to leave an edge."""
    spot_cost: float = SPOT_COST
    long_venue: str = "spot"     # spot | perp
    carry_on: bool = True

    @staticmethod
    def for_spot_fee(maker: float, taker: float) -> "CostModel":
        cost = maker + 0.0005
        return CostModel(spot_cost=cost, long_venue="spot" if taker <= 0.0025 else "perp",
                         carry_on=maker <= 0.0040)


def compute_all(store: HistoryStore, alt: AltData, P_long=None, P_short=None, panels=None,
                costs: CostModel = CostModel()) -> DeskState:
    """The whole team, causally, over all history. Row i = decisions at the close of day i."""
    d = load_daily(store, alt)
    reg = regime(d)
    pl, ps = (panels or (None, None))
    o_long, x_long = oracle_sleeve(d, store, alt, side="long", P=P_long, panel=pl)
    o_short, x_short = oracle_sleeve(d, store, alt, side="short", P=P_short, panel=ps)
    W = {"btc_regime": strat_btc_regime(d), "trend": strat_trend(d), "rotation": strat_rotation(d, reg),
         "breakout": strat_breakout(d, reg), "hodl": strat_hodl(d)}
    carry_w = strat_carry(d) if costs.carry_on else np.zeros(d.C.shape)
    sc = costs.spot_cost
    lib = {
        "hodl": sleeve_returns(d, W["hodl"], spot=True, spot_cost=sc),
        "btc_regime": sleeve_returns(d, W["btc_regime"], spot=costs.long_venue == "spot", spot_cost=sc),
        "trend": sleeve_returns(d, W["trend"]),
        "rotation": sleeve_returns(d, W["rotation"]),
        "breakout": sleeve_returns(d, W["breakout"]),
        "carry": sleeve_returns(d, np.zeros(d.C.shape), carry_w, spot_cost=sc),
        "oracle_long": o_long,
        "oracle_short": o_short,
    }
    btc_on = daily_regime(d)
    atlas = agent_atlas(d, lib, btc_on)
    x = np.clip(x_long + x_short, 0, 1)
    idle = np.clip(1 - np.roll(x, 1), 0, 1)
    oracle_agent = lib["oracle_long"] + lib["oracle_short"] + idle * lib["carry"]
    nova, _, nova_w = agent_nova(d, lib, NOVA_UNIVERSE)
    agents = {"ATLAS": atlas, "ORACLE": oracle_agent, "NOVA": nova}
    team_r, desk_w, _ = team(d, agents)
    return DeskState(d, lib, W, carry_w, btc_on, agents, nova_w, desk_w, team_r, x)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--spot-maker", type=float, default=None, help="e.g. 0.006 for a small Coinbase account")
    ap.add_argument("--spot-taker", type=float, default=None)
    a = ap.parse_args()
    costs = CostModel() if a.spot_maker is None else CostModel.for_spot_fee(a.spot_maker, a.spot_taker or a.spot_maker * 2)
    print("cost model:", costs)
    store = HistoryStore()
    store.load()
    alt = AltData()
    alt.load()
    t0 = time.time()
    st = compute_all(store, alt, costs=costs)
    d = st.d
    rows = {**{f"strategy:{k}": v for k, v in st.lib.items()},
            **{f"agent:{k}": v for k, v in st.agents.items()}, "TEAM (QUORUM)": st.team}
    res = {k: stats(d, v) for k, v in rows.items()}
    print(f"built in {time.time() - t0:.0f}s · {time.strftime('%Y-%m-%d', time.gmtime(START))} .. "
          f"{time.strftime('%Y-%m-%d', time.gmtime(int(d.days[-1]) * DAY))}\n")
    print(f"{'':<22}{'total':>9}{'maxDD':>8}{'Sharpe':>8}{'up/down months':>16}{'worst mo':>10}  yearly %")
    for k, v in res.items():
        yr = {y: float(x) for y, x in v["yearly_pct"].items()}
        print(f"{k:<22}{v['total_pct']:>8.1f}%{v['max_dd_pct']:>7.1f}%{v['sharpe']:>8.2f}"
              f"{v['up_months']:>9}/{v['down_months']:<6}{v['worst_month_pct']:>9.1f}%  {yr}")
    print("\ndesk weights (avg):", dict(zip(["ATLAS", "ORACLE", "NOVA"], np.round(st.desk_w.mean(axis=0), 2))))
    if a.json:
        import json as _json
        from pathlib import Path
        Path(a.json).write_text(_json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
