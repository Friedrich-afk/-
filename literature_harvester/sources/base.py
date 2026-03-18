"""Source plugin interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Iterable


class Source(ABC):
    name: str

    def __init__(self, config: Dict):
        self.config = config
        self.name = config.get("name", self.__class__.__name__)

    @abstractmethod
    def search(self, query: Dict) -> Iterable[Dict]:
        """Yield publication dictionaries."""


class SourceRegistry:
    def __init__(self) -> None:
        self._sources = {}

    def register(self, key: str, cls: type[Source]) -> None:
        self._sources[key] = cls

    def create(self, key: str, config: Dict) -> Source:
        if key not in self._sources:
            raise KeyError(f"Unknown source type: {key}")
        return self._sources[key](config)

    def available(self) -> Dict[str, str]:
        return {key: cls.__name__ for key, cls in self._sources.items()}


registry = SourceRegistry()
