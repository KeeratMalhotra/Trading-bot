"""SQLite persistence so the battle survives restarts (multi-day streams)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, bot TEXT, closed REAL, data TEXT);
CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, ts REAL, bot TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS equity (bot TEXT, ts REAL, equity REAL);
CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_equity ON equity(bot, ts);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def kv_get(self, key: str):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def kv_set(self, key: str, value) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    def add_trades(self, trades: list[dict]) -> None:
        if not trades:
            return
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO trades(id, bot, closed, data) VALUES(?,?,?,?)",
                                [(t["id"], t["bot"], t["closed"], json.dumps(t)) for t in trades])

    def trades(self, bot: str | None = None, limit: int = 500) -> list[dict]:
        if bot:
            rows = self.db.execute("SELECT data FROM trades WHERE bot=? ORDER BY closed DESC LIMIT ?", (bot, limit))
        else:
            rows = self.db.execute("SELECT data FROM trades ORDER BY closed DESC LIMIT ?", (limit,))
        return [json.loads(r[0]) for r in rows]

    def add_events(self, events: list[dict]) -> None:
        if not events:
            return
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO events(id, ts, bot, data) VALUES(?,?,?,?)",
                                [(e["id"], e["ts"], e["bot"], json.dumps(e)) for e in events])

    def events(self, limit: int = 300) -> list[dict]:
        rows = self.db.execute("SELECT data FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return [json.loads(r[0]) for r in rows][::-1]

    def add_equity(self, rows: list[tuple[str, float, float]]) -> None:
        with self.lock, self.db:
            self.db.executemany("INSERT INTO equity(bot, ts, equity) VALUES(?,?,?)", rows)

    def equity(self, since: float) -> dict[str, list[tuple[float, float]]]:
        out: dict[str, list[tuple[float, float]]] = {}
        for bot, ts, eq in self.db.execute("SELECT bot, ts, equity FROM equity WHERE ts>=? ORDER BY ts", (since,)):
            out.setdefault(bot, []).append((ts, eq))
        return out

    def prune(self, now: float) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM events WHERE ts < ?", (now - 14 * 86400,))
            self.db.execute("DELETE FROM equity WHERE ts < ?", (now - 60 * 86400,))

    def clear(self) -> None:
        with self.lock, self.db:
            for t in ("kv", "trades", "events", "equity"):
                self.db.execute(f"DELETE FROM {t}")
