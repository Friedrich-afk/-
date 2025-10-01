"""Query orchestration logic."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

from .deduplication import DeduplicatedItem, Deduplicator
from .database import SeenDatabase
from . import sources  # noqa: F401  ensures plugin registration
from .sources.base import registry

DEFAULT_SOURCES = [
    {
        "key": "crossref",
        "name": "Crossref",
        "rows": 50,
    },
    {
        "key": "arxiv",
        "name": "arXiv",
        "max_results": 50,
    },
]


class SourceManager:
    """Handles persistence and instantiation of source configurations."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        if not self.config_path.exists():
            self._write(DEFAULT_SOURCES)

    def _write(self, data: List[Dict]) -> None:
        self.config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load(self) -> List[Dict]:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def save(self, sources: List[Dict]) -> None:
        self._write(sources)


class SearchEngine:
    def __init__(self, *, seen_path: Path, sources_config: Path) -> None:
        self.seen_db = SeenDatabase(seen_path)
        self.deduplicator = Deduplicator(self.seen_db)
        self.source_manager = SourceManager(sources_config)

    def available_sources(self) -> List[Dict]:
        sources = self.source_manager.load()
        for source in sources:
            source.setdefault("name", source.get("key"))
        return sources

    def update_sources(self, sources: List[Dict]) -> List[Dict]:
        self.source_manager.save(sources)
        return self.available_sources()

    def search(self, query: Dict) -> List[Dict]:
        selected_keys = query.get("source_keys") or [s["key"] for s in self.available_sources()]
        results: List[DeduplicatedItem] = []
        for source_config in self.available_sources():
            if source_config["key"] not in selected_keys:
                continue
            try:
                source = registry.create(source_config["key"], source_config)
            except KeyError:
                continue
            for item in source.search(query):
                if not self._matches_scope(query, item):
                    continue
                results.append(DeduplicatedItem(source=source_config.get("name", ""), raw=item))
        fresh = self.deduplicator.filter_new(results)
        return [self._augment(item.raw, item.source) for item in fresh]

    @staticmethod
    def _matches_scope(query: Dict, item: Dict) -> bool:
        scopes = query.get("scopes")
        if not scopes:
            return True
        return item.get("type") in scopes or item.get("publisher") in scopes or item.get("source") in scopes

    @staticmethod
    def _augment(item: Dict, source_name: str) -> Dict:
        item = dict(item)
        item.setdefault("source", source_name)
        return item


__all__ = ["SearchEngine"]
