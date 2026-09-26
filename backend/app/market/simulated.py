"""Offline fallback feed: regime-switching random walk. Clearly labelled SIMULATED in the UI.

Used only when Coinbase is unreachable (or MARKET_SOURCE=sim).
"""
from __future__ import annotations

import asyncio
import random
import time

import numpy as np

from ..config import TIMEFRAMES
from .candles import Candle
from .hub import MarketHub

BASE = {"BTC-USD": 84000, "ETH-USD": 3200, "SOL-USD": 190, "XRP-USD": 2.6, "DOGE-USD": 0.22,
        "ADA-USD": 0.75, "AVAX-USD": 28, "LINK-USD": 20, "LTC-USD": 105, "SUI-USD": 3.4}
VOL_1M = {"BTC-USD": 0.0009, "ETH-USD": 0.0012}  # per-minute sigma
VOL_OTHER = 0.0018


def _path(n: int, sigma: float, rng: np.random.Generator) -> np.ndarray:
    drift = np.zeros(n)
    vol = np.full(n, sigma)
    i = 0
    while i < n:  # regime blocks: trend up / trend down / range / volatile
        length = int(rng.integers(240, 2400))
        kind = rng.choice(["up", "down", "range", "wild"], p=[0.3, 0.25, 0.35, 0.1])
        mu = {"up": sigma * 0.08, "down": -sigma * 0.08, "range": 0.0, "wild": 0.0}[kind]
        drift[i:i + length] = mu
        vol[i:i + length] = sigma * (2.2 if kind == "wild" else rng.uniform(0.7, 1.2))
        i += length
    rets = drift + vol * rng.standard_normal(n)
    return np.exp(np.cumsum(rets))


def seed_history(hub: MarketHub, now: float) -> None:
    rng = np.random.default_rng(int(now) // 86400)
    minutes = 300 * TIMEFRAMES[-1] // 60 + 300
    start = (int(now) // 60 - minutes) * 60
    for sym in hub.symbols:
        sigma = VOL_1M.get(sym, VOL_OTHER)
        closes = BASE.get(sym, 10) * _path(minutes, sigma, rng)
        closes *= BASE.get(sym, 10) / closes[-1]  # end at the base price
        opens = np.concatenate([[closes[0]], closes[:-1]])
        wig = np.abs(rng.standard_normal(minutes)) * sigma * 0.6 * closes
        highs = np.maximum(opens, closes) + wig
        lows = np.minimum(opens, closes) - wig
        vols = rng.lognormal(0, 0.6, minutes) * 100_000 / closes
        for tf, series in hub.series[sym].items():
            k = tf // 60
            n = minutes // k
            off = minutes - n * k
            candles = []
            for j in range(n):
                a, b = off + j * k, off + (j + 1) * k
                candles.append(Candle(start + a * 60, float(opens[a]), float(highs[a:b].max()),
                                      float(lows[a:b].min()), float(closes[b - 1]), float(vols[a:b].sum())))
            series.seed(candles[-600:], now)
        q = hub.quotes[sym]
        q.price = q.bid = q.ask = float(closes[-1])
        q.open_24h = float(closes[-1440])
        q.ts = now


async def run_sim(hub: MarketHub, stop: asyncio.Event) -> None:
    hub.status.source = "sim"
    hub.status.connected = True
    hub.status.message = "SIMULATED market data (Coinbase unreachable)"
    state = {s: {"mu": 0.0, "left": 0} for s in hub.symbols}
    while not stop.is_set():
        now = time.time()
        for sym in hub.symbols:
            st = state[sym]
            if st["left"] <= 0:
                sigma = VOL_1M.get(sym, VOL_OTHER)
                st["mu"] = random.choice([0.06, -0.05, 0.0, 0.0]) * sigma / 8
                st["left"] = random.randint(1200, 12000)
            st["left"] -= 1
            q = hub.quotes[sym]
            sig_s = VOL_1M.get(sym, VOL_OTHER) / 8  # ~per 1s
            price = q.price * float(np.exp(st["mu"] + sig_s * random.gauss(0, 1)))
            size = random.lognormvariate(0, 1) * 500 / price
            hub.on_trade(sym, price, size, now)
        await asyncio.sleep(0.5)
