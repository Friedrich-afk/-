"""Deduplication helpers for the literature harvester."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .database import SeenDatabase


@dataclass
class DeduplicatedItem:
    """Container for deduplicated publication results."""

    source: str
    raw: Dict

    @property
    def doi(self) -> str | None:
        return self.raw.get("doi")

    @property
    def title(self) -> str | None:
        return self.raw.get("title")

    @property
    def year(self) -> str | None:
        return self.raw.get("year")

    @property
    def url(self) -> str | None:
        return self.raw.get("url")

    @property
    def url_hash(self) -> str | None:
        if not self.url:
            return None
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()


class Deduplicator:
    """Performs DOI -> (title+year) -> URL hash deduplication."""

    def __init__(self, seen_db: SeenDatabase) -> None:
        self.seen_db = seen_db

    def filter_new(self, items: Iterable[DeduplicatedItem]) -> List[DeduplicatedItem]:
        fresh: List[DeduplicatedItem] = []
        for item in items:
            if self.seen_db.is_seen(
                doi=item.doi,
                title=item.title,
                year=item.year,
                url_hash=item.url_hash,
            ):
                continue
            fresh.append(item)
        # Persist once per batch to minimize IO
        self.seen_db.bulk_mark_seen(
            {
                "doi": item.doi,
                "title": item.title,
                "year": item.year,
                "url_hash": item.url_hash,
            }
            for item in fresh
        )
        return fresh
