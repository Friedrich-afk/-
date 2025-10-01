"""arXiv search source."""
from __future__ import annotations

import feedparser
from typing import Dict, Iterable
from urllib.parse import urlencode

from .base import Source, registry


class ArxivSource(Source):
    API_URL = "http://export.arxiv.org/api/query"

    def search(self, query: Dict) -> Iterable[Dict]:
        search_terms = []
        if query.get("keywords"):
            search_terms.append("all:" + " AND ".join(query["keywords"].split()))
        if query.get("authors"):
            for author in query["authors"]:
                search_terms.append(f"au:{author}")
        params = {
            "search_query": " AND ".join(search_terms) if search_terms else "all:*",
            "start": 0,
            "max_results": self.config.get("max_results", 50),
            "sortBy": "submittedDate",
        }
        response = feedparser.parse(f"{self.API_URL}?{urlencode(params)}")
        for entry in response.entries:
            year = entry.published_parsed.tm_year if entry.get("published_parsed") else None
            language = "English"
            if query.get("languages") and language not in query["languages"]:
                continue
            doc_type = "preprint"
            if query.get("formats") and doc_type not in query["formats"]:
                continue
            yield {
                "source": self.name,
                "title": entry.get("title", "").strip(),
                "authors": [author.name for author in entry.get("authors", [])],
                "doi": entry.get("arxiv_doi"),
                "publisher": "arXiv",
                "year": str(year) if year else None,
                "language": language,
                "type": doc_type,
                "url": entry.get("id"),
                "abstract": entry.get("summary"),
            }


registry.register("arxiv", ArxivSource)
