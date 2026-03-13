"""SQLite storage helpers for the literature harvester application."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    title TEXT,
    year TEXT,
    url_hash TEXT,
    UNIQUE(doi)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_title_year ON seen(title, year);
CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_url_hash ON seen(url_hash);
"""


class SeenDatabase:
    """Simple wrapper around SQLite for storing seen publication identifiers."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    def mark_seen(
        self,
        *,
        doi: Optional[str],
        title: Optional[str],
        year: Optional[str],
        url_hash: Optional[str],
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen (doi, title, year, url_hash) VALUES (?, ?, ?, ?)",
                (doi, title, year, url_hash),
            )

    def is_seen(
        self,
        *,
        doi: Optional[str],
        title: Optional[str],
        year: Optional[str],
        url_hash: Optional[str],
    ) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            if doi:
                cur = conn.execute("SELECT 1 FROM seen WHERE doi = ? LIMIT 1", (doi,))
                if cur.fetchone():
                    return True
            if title and year:
                cur = conn.execute(
                    "SELECT 1 FROM seen WHERE title = ? AND year = ? LIMIT 1",
                    (title, year),
                )
                if cur.fetchone():
                    return True
            if url_hash:
                cur = conn.execute(
                    "SELECT 1 FROM seen WHERE url_hash = ? LIMIT 1",
                    (url_hash,),
                )
                if cur.fetchone():
                    return True
        return False

    def bulk_mark_seen(self, entries: Iterable[dict]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen (doi, title, year, url_hash) VALUES (?, ?, ?, ?)",
                (
                    (
                        entry.get("doi"),
                        entry.get("title"),
                        entry.get("year"),
                        entry.get("url_hash"),
                    )
                    for entry in entries
                ),
            )
