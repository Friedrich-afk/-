"""Crossref search source."""
from __future__ import annotations

from typing import Dict, Iterable, List

import requests

from .base import Source, registry


class CrossrefSource(Source):
    API_URL = "https://api.crossref.org/works"

    def search(self, query: Dict) -> Iterable[Dict]:
        params = {
            "query": query.get("keywords"),
            "rows": self.config.get("rows", 50),
        }
        filters: List[str] = []
        if query.get("start_date"):
            filters.append(f"from-pub-date:{query['start_date']}")
        if query.get("end_date"):
            filters.append(f"until-pub-date:{query['end_date']}")
        if query.get("authors"):
            params["query.author"] = " ".join(query["authors"])
        if filters:
            params["filter"] = ",".join(filters)
        response = requests.get(self.API_URL, params=params, timeout=30)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
        for item in items:
            title = item.get("title") or [""]
            authors = [
                " ".join(filter(None, [person.get("given"), person.get("family")]))
                for person in item.get("author", [])
            ]
            published = item.get("published-print") or item.get("published-online")
            year = None
            if published:
                date_parts = published.get("date-parts")
                if date_parts:
                    year = str(date_parts[0][0])
            language = item.get("language") or ""
            if query.get("languages") and language not in query["languages"]:
                continue
            doc_type = item.get("type") or ""
            if query.get("formats") and doc_type not in query["formats"]:
                continue
            url = item.get("URL")
            yield {
                "source": self.name,
                "title": title[0] if title else "",
                "authors": authors,
                "doi": item.get("DOI"),
                "publisher": item.get("publisher"),
                "year": year,
                "language": language,
                "type": doc_type,
                "url": url,
                "abstract": item.get("abstract"),
            }


registry.register("crossref", CrossrefSource)
