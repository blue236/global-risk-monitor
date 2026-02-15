from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, List, Tuple


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS series_observations (
  series_id TEXT NOT NULL,
  date TEXT NOT NULL,
  value REAL NOT NULL,
  PRIMARY KEY(series_id, date)
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._init()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(SCHEMA)

    def upsert_observations(self, series_id: str, rows: Iterable[Tuple[str, float]]) -> int:
        """Upsert rows (date_iso, value). Returns number of rows inserted/updated."""
        rows_list = list(rows)
        if not rows_list:
            return 0
        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO series_observations(series_id, date, value)
                VALUES(?, ?, ?)
                ON CONFLICT(series_id, date) DO UPDATE SET value=excluded.value
                """,
                [(series_id, d, v) for d, v in rows_list],
            )
            return con.total_changes

    def fetch_series(self, series_id: str, limit: int = 4000) -> List[Tuple[str, float]]:
        """Return the most recent `limit` rows in ascending date order."""
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT date, value FROM (
                  SELECT date, value FROM series_observations
                  WHERE series_id=?
                  ORDER BY date DESC
                  LIMIT ?
                ) t
                ORDER BY date ASC
                """,
                (series_id, limit),
            )
            return [(r["date"], float(r["value"])) for r in cur.fetchall()]

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        with self._connect() as con:
            cur = con.execute("SELECT value FROM meta WHERE key=?", (key,))
            row = cur.fetchone()
            return row["value"] if row else None

