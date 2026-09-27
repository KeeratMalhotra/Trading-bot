"""Alternative data: perpetual-futures funding rates and the Crypto Fear & Greed Index.

Funding history: Deribit public API (hourly, BTC/ETH since 2019; USDC-linear alts since ~2022).
It's used as a PROXY for Coinbase US perpetual-style futures funding, whose history isn't public.
Coins without their own history fall back to BTC's funding.

Coinbase's own hourly rates are recorded live by app.team.funding from the day the app first
runs; wherever a recorded Coinbase rate exists it takes priority over the Deribit proxy.

Long backtests: `python -m app.ml.altdata --since 2016-01-01` extends BTC/ETH funding back
with Deribit (from 2019) and BitMEX (XBTUSD from 2016, ETHUSD from 2018) before that.

Fear & Greed: alternative.me daily index since 2018 (free, no key).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np

from ..config import DATA_DIR

log = logging.getLogger("ml.altdata")
DERIBIT = "https://www.deribit.com/api/v2/public/get_funding_rate_history"
INSTRUMENTS = {
    "BTC-USD": "BTC-PERPETUAL", "ETH-USD": "ETH-PERPETUAL",
    "SOL-USD": "SOL_USDC-PERPETUAL", "XRP-USD": "XRP_USDC-PERPETUAL",
    "DOGE-USD": "DOGE_USDC-PERPETUAL", "ADA-USD": "ADA_USDC-PERPETUAL",
    "AVAX-USD": "AVAX_USDC-PERPETUAL", "LINK-USD": "LINK_USDC-PERPETUAL",
    "LTC-USD": "LTC_USDC-PERPETUAL",
}
START = 1577836800  # 2020-01-01
BITMEX = "https://www.bitmex.com/api/v1/funding"
BITMEX_SYMBOLS = {"BTC-USD": "XBTUSD", "ETH-USD": "ETHUSD"}   # only used by the --since backfill
CB_PREFIX = "coinbase-funding-"


class AltData:
    def __init__(self, root: Path | None = None):
        self.root = root or (DATA_DIR / "altdata")
        self.root.mkdir(parents=True, exist_ok=True)
        self.funding: dict[str, np.ndarray] = {}  # symbol -> (n, 2) [hour_ts, hourly_rate]  (Deribit proxy)
        self.cb: dict[str, np.ndarray] = {}       # symbol -> (n, 2) [hour_ts, hourly_rate]  (Coinbase, recorded live)
        self.fng: np.ndarray = np.zeros((0, 2))   # (n, 2) [day_ts, value 0..100]

    # ------------------------------------------------------------ load/save
    def load(self) -> None:
        for s in INSTRUMENTS:
            p = self.root / f"funding-{s}.npy"
            self.funding[s] = np.load(p) if p.exists() else np.zeros((0, 2))
        for p in self.root.glob(f"{CB_PREFIX}*.npy"):
            self.cb[p.stem[len(CB_PREFIX):]] = np.load(p)
        p = self.root / "fear_greed.npy"
        self.fng = np.load(p) if p.exists() else np.zeros((0, 2))

    def add_coinbase_funding(self, symbol: str, hour: int, rate: float) -> bool:
        """Record Coinbase's rate for the funding hour starting at `hour`. Returns True if new/changed."""
        arr = self.cb.get(symbol, np.zeros((0, 2)))
        i = int(np.searchsorted(arr[:, 0], hour)) if len(arr) else 0
        if i < len(arr) and arr[i, 0] == hour:
            if arr[i, 1] == rate:
                return False
            arr = arr.copy()
            arr[i, 1] = rate
        else:
            arr = np.insert(arr, i, [hour, rate], axis=0)
        self.cb[symbol] = arr                 # swap in a new array (readers in other threads stay consistent)
        np.save(self.root / f"{CB_PREFIX}{symbol}.npy", arr)
        return True

    async def update(self) -> None:
        if not self.funding:
            self.load()
        sem = asyncio.Semaphore(3)            # a few coins at a time: the first download is ~3x faster
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "quorum/1.0"}) as client:
            async def one(s: str, inst: str) -> None:
                async with sem:
                    try:
                        await self._update_funding(client, s, inst)
                    except Exception as e:  # noqa: BLE001
                        log.warning("funding %s failed: %s", s, e)
            await asyncio.gather(*(one(s, inst) for s, inst in INSTRUMENTS.items()))
            try:
                r = await client.get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"})
                rows = [(int(x["timestamp"]), float(x["value"])) for x in r.json()["data"]]
                self.fng = np.array(sorted(rows), dtype=float)
                np.save(self.root / "fear_greed.npy", self.fng)
            except Exception as e:  # noqa: BLE001
                log.warning("fear&greed failed: %s", e)

    async def _update_funding(self, client: httpx.AsyncClient, s: str, inst: str) -> None:
        arr = self.funding.get(s, np.zeros((0, 2)))
        start = int(arr[-1, 0]) + 3600 if len(arr) else START
        end = int(time.time() // 3600 * 3600)
        rows = await self._deribit_range(client, inst, start, end, stop_on_gap=bool(len(arr)))
        self._merge_funding(s, rows)

    @staticmethod
    async def _deribit_range(client: httpx.AsyncClient, inst: str, start: int, end: int,
                             stop_on_gap: bool = True) -> dict[int, float]:
        rows: dict[int, float] = {}
        t = start
        while t < end:
            b = min(t + 30 * 86400, end)
            r = await client.get(DERIBIT, params={"instrument_name": inst, "start_timestamp": t * 1000,
                                                  "end_timestamp": b * 1000})
            d = r.json()
            if "result" not in d:
                if t == start or not stop_on_gap:
                    t = b
                    continue
                break
            for x in d["result"]:
                rows[int(x["timestamp"] // 1000 // 3600 * 3600)] = float(x["interest_1h"])
            t = b
            await asyncio.sleep(0.15)
        return rows

    @staticmethod
    async def _bitmex_range(client: httpx.AsyncClient, symbol: str, start: int, end: int) -> dict[int, float]:
        """BitMEX funding (8-hourly; early XBTUSD daily) spread evenly over the hours each payment covered."""
        rows: dict[int, float] = {}
        t = start
        while t < end:
            since = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            r = await client.get(BITMEX, params={"symbol": symbol, "count": 500, "reverse": "false", "startTime": since})
            if r.status_code == 429:
                await asyncio.sleep(10)
                continue
            r.raise_for_status()
            xs = r.json()
            if not xs:
                break
            last = t
            for x in xs:
                ts = int(datetime.fromisoformat(x["timestamp"].replace("Z", "+00:00")).timestamp())
                iv = x["fundingInterval"]               # e.g. "2000-01-01T08:00:00.000Z" = 8 hours
                hours = 24 * (int(iv[8:10]) - 1) + int(iv[11:13]) or 8
                for k in range(1, hours + 1):
                    h = ts - k * 3600
                    if start <= h < end:
                        rows[h] = float(x["fundingRate"]) / hours
                last = ts
            if last < t + 1:
                break
            t = last + 1
            await asyncio.sleep(2.1)                      # BitMEX public rate limit
        return rows

    def _merge_funding(self, s: str, rows: dict[int, float]) -> None:
        if not rows:
            return
        arr = self.funding.get(s, np.zeros((0, 2)))
        new = np.array(sorted(rows.items()), dtype=float)
        merged = np.vstack([arr, new]) if len(arr) else new
        _, idx = np.unique(merged[:, 0], return_index=True)
        self.funding[s] = merged[idx]
        np.save(self.root / f"funding-{s}.npy", self.funding[s])

    async def backfill(self, since: float) -> None:
        """One-off, for long backtests: extend BTC/ETH funding back to `since`. Deribit covers
        2019 onward; BitMEX fills the years before. Other coins fall back to BTC's funding."""
        if not self.funding:
            self.load()
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "quorum/1.0"}) as client:
            for s, bmx in BITMEX_SYMBOLS.items():
                arr = self.funding.get(s, np.zeros((0, 2)))
                end = int(arr[0, 0]) if len(arr) else int(time.time() // 3600 * 3600)
                start = int(since // 3600 * 3600)
                if start >= end:
                    continue
                rows = await self._deribit_range(client, INSTRUMENTS[s], start, end, stop_on_gap=False)
                first = min(rows) if rows else end
                if start < first:
                    rows = {**await self._bitmex_range(client, bmx, start, first), **rows}
                self._merge_funding(s, rows)
                log.info("%s funding now starts %s", s, time.strftime("%Y-%m-%d", time.gmtime(self.funding[s][0, 0])))

    # ------------------------------------------------------------ access
    def hourly_funding(self, symbol: str, hours: np.ndarray) -> np.ndarray:
        """Hourly funding rate (fraction; positive = longs pay shorts) aligned to `hours`.
        Coinbase's recorded rate where we have it, otherwise the Deribit proxy."""
        out = self._proxy_funding(symbol, hours)
        cb = self.cb.get(symbol)
        if cb is not None and len(cb) and len(hours):
            idx = np.clip(np.searchsorted(cb[:, 0], hours), 0, len(cb) - 1)
            hit = cb[idx, 0] == hours
            out[hit] = cb[idx[hit], 1]
        return out

    def funding_at(self, symbol: str, hour: int, live: bool = False) -> tuple[float, str]:
        """Rate for one funding hour and where it came from ("coinbase" | "deribit").
        live=True: if Coinbase hasn't published this hour yet, use its latest rate (<= 3h old)."""
        cb = self.cb.get(symbol)
        if cb is not None and len(cb):
            i = int(np.searchsorted(cb[:, 0], hour))
            if i < len(cb) and cb[i, 0] == hour:
                return float(cb[i, 1]), "coinbase"
            if live and i > 0 and hour - cb[i - 1, 0] <= 3 * 3600:
                return float(cb[i - 1, 1]), "coinbase"
        return float(self._proxy_funding(symbol, np.array([hour]))[0]), "deribit"

    def _proxy_funding(self, symbol: str, hours: np.ndarray) -> np.ndarray:
        own = self.funding.get(symbol)
        btc = self.funding.get("BTC-USD")
        out = np.full(len(hours), np.nan)
        for src in (own, btc):
            if src is None or not len(src):
                continue
            idx = np.searchsorted(src[:, 0], hours, side="right") - 1
            ok = (idx >= 0) & np.isnan(out)
            ok &= np.abs(src[np.clip(idx, 0, None), 0] - hours) <= 6 * 3600
            out[ok] = src[idx[ok], 1]
        return np.nan_to_num(out, nan=0.0)

    def fear_greed_at(self, ts: float) -> float | None:
        if not len(self.fng):
            return None
        i = np.searchsorted(self.fng[:, 0], ts - 86400, side="right") - 1  # yesterday's (published) value
        return float(self.fng[i, 1]) if i >= 0 else None

    def summary(self) -> dict:
        out = {s: {"hours": int(len(a)), "from": time.strftime("%Y-%m-%d", time.gmtime(a[0, 0])),
                   "mean_annual_pct": round(float(a[:, 1].mean() * 24 * 365 * 100), 1)}
               for s, a in self.funding.items() if len(a)}
        if len(self.fng):
            out["fear_greed"] = {"days": int(len(self.fng)), "from": time.strftime("%Y-%m-%d", time.gmtime(self.fng[0, 0]))}
        cb = {s: int(len(a)) for s, a in self.cb.items() if len(a)}
        if cb:
            out["coinbase_hours_recorded"] = cb
        return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="also backfill BTC/ETH funding back to this date, e.g. 2016-01-01")
    args = ap.parse_args()
    a = AltData()
    t0 = time.time()
    asyncio.run(a.update())
    if args.since:
        asyncio.run(a.backfill(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()))
    print(f"{time.time() - t0:.0f}s", json.dumps(a.summary(), indent=1))
