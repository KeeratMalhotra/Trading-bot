"""Hourly candle history store (Coinbase REST), cached on disk and updated incrementally.

This is the single data source for ORACLE: training, walk-forward research and live
predictions all read the same REST candles, so what the model sees live is exactly
what it was trained on.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from ..config import DATA_DIR, SYMBOLS
from ..market.coinbase import HEADERS, fetch_candles

log = logging.getLogger("ml.data")

TF = 3600
HISTORY_START = 1577836800  # 2020-01-01 UTC
MAX_FILL_GAP = 6            # hours: small gaps are forward-filled, bigger gaps split the series


@dataclass
class Series:
    """Contiguous hourly OHLCV arrays (UTC hour starts)."""
    symbol: str
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    def slice(self, sl: slice) -> "Series":
        return Series(self.symbol, self.t[sl], self.o[sl], self.h[sl], self.l[sl], self.c[sl], self.v[sl])


class HistoryStore:
    def __init__(self, root: Path | None = None, symbols: list[str] | None = None):
        self.root = root or (DATA_DIR / "history")
        self.root.mkdir(parents=True, exist_ok=True)
        self.symbols = symbols or list(SYMBOLS)
        self.raw: dict[str, np.ndarray] = {}   # symbol -> (n, 6) [t, o, h, l, c, v]
        self.progress = 0.0

    # ------------------------------------------------------------ disk
    def _path(self, symbol: str) -> Path:
        return self.root / f"{symbol}-1h.npy"

    def load(self) -> None:
        for s in self.symbols:
            p = self._path(s)
            self.raw[s] = np.load(p) if p.exists() else np.zeros((0, 6))

    def _save(self, symbol: str) -> None:
        np.save(self._path(symbol), self.raw[symbol])

    # ---------------------------------------------------------- update
    async def update(self, until: float | None = None, concurrency: int = 3) -> None:
        """Fetch everything missing between HISTORY_START and now (closed candles only)."""
        if not self.raw:
            self.load()
        end = int((until or time.time()) // TF * TF)  # current (forming) hour excluded
        sem = asyncio.Semaphore(concurrency)
        total = {s: 0 for s in self.symbols}
        done = {s: 0 for s in self.symbols}

        async def one(client: httpx.AsyncClient, s: str) -> None:
            arr = self.raw[s]
            # re-fetch the last few hours too: the newest candle may not have been final
            start = int(arr[-1, 0]) - 2 * TF if len(arr) else HISTORY_START
            if start >= end:
                return
            chunks = list(range(start, end, TF * 300))
            total[s] = len(chunks)
            rows: dict[int, list[float]] = {}
            for a in chunks:
                b = min(a + TF * 300, end)
                async with sem:
                    for attempt in range(4):
                        try:
                            cs = await fetch_candles(client, s, TF, a, b)
                            break
                        except Exception as e:  # noqa: BLE001
                            if attempt == 3:
                                raise
                            log.warning("retry %s %s: %s", s, a, e)
                            await asyncio.sleep(1.5 * (attempt + 1))
                    await asyncio.sleep(0.11)
                for c in cs:
                    if a <= c.t < b:
                        rows[c.t] = [c.t, c.o, c.h, c.l, c.c, c.v]
                done[s] += 1
                self.progress = sum(done.values()) / max(sum(total.values()), 1)
            if rows:
                new = np.array([rows[k] for k in sorted(rows)], dtype=float)
                merged = np.vstack([new, arr]) if len(arr) else new  # new rows win duplicates
                _, idx = np.unique(merged[:, 0], return_index=True)
                self.raw[s] = merged[idx]
                self._save(s)

        async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
            await asyncio.gather(*(one(client, s) for s in self.symbols))
        self.progress = 1.0

    # ---------------------------------------------------------- access
    def series(self, symbol: str, until: float | None = None) -> list[Series]:
        """Contiguous segments (small gaps forward-filled, zero volume)."""
        arr = self.raw.get(symbol)
        if arr is None or len(arr) == 0:
            return []
        if until is not None:
            arr = arr[arr[:, 0] < until]
        if len(arr) == 0:
            return []
        t = arr[:, 0].astype(np.int64)
        gaps = np.diff(t) // TF
        cut = np.where(gaps > MAX_FILL_GAP)[0]
        segs, lo = [], 0
        for k in list(cut) + [len(t) - 1]:
            segs.append(arr[lo:k + 1])
            lo = k + 1
        out = []
        for seg in segs:
            st = seg[:, 0].astype(np.int64)
            full_t = np.arange(st[0], st[-1] + TF, TF, dtype=np.int64)
            idx = np.searchsorted(st, full_t, side="right") - 1
            present = st[idx] == full_t
            c = seg[idx, 4]
            o = np.where(present, seg[idx, 1], c)
            h = np.where(present, seg[idx, 2], c)
            l = np.where(present, seg[idx, 3], c)
            v = np.where(present, seg[idx, 5], 0.0)
            out.append(Series(symbol, full_t, o, h, l, c, v))
        return out

    def latest_segment(self, symbol: str, until: float | None = None) -> Series | None:
        segs = self.series(symbol, until)
        return segs[-1] if segs else None

    def summary(self) -> dict:
        out = {}
        for s, arr in self.raw.items():
            if len(arr):
                out[s] = {"bars": int(len(arr)), "from": int(arr[0, 0]), "to": int(arr[-1, 0])}
        return out


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    st = HistoryStore()
    t0 = time.time()
    asyncio.run(st.update())
    print(f"done in {time.time() - t0:.0f}s")
    for s, d in st.summary().items():
        print(s, d["bars"], time.strftime("%Y-%m-%d", time.gmtime(d["from"])), "->",
              time.strftime("%Y-%m-%d %H:%M", time.gmtime(d["to"])))


if __name__ == "__main__":
    main()
