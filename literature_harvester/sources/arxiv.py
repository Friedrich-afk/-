"""arXiv search source."""
from __future__ import annotations

import feedparser
from typing import Dict, Iterable
from urllib.parse import urlencode

from .base import Source, registry
from .utils import LANGUAGE_LABELS, language_display, normalize_language_code


class ArxivSource(Source):
    API_URL = "http://export.arxiv.org/api/query"

    def search(self, query: Dict) -> Iterable[Dict]:
        search_terms = []
        keywords = (query.get("keywords") or "").replace("，", " ").split()
        search_mode = (query.get("search_mode") or "full_text").lower()
        if search_mode not in {"full_text", "keywords", "title"}:
            search_mode = "full_text"
        if keywords:
            field_map = {
                "full_text": "all",
                "keywords": "abs",
                "title": "ti",
            }
            prefix = field_map.get(search_mode, "all")
            search_terms.append(" AND ".join(f"{prefix}:{term}" for term in keywords))
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
        requested_languages = {
            lang
            for lang in (
                normalize_language_code(language)
                for language in query.get("languages", [])
            )
            if lang
        }
        for entry in response.entries:
            year = entry.published_parsed.tm_year if entry.get("published_parsed") else None
            language_code = "en"
            if requested_languages and language_code not in requested_languages:
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
                "language": language_code,
                "language_display": language_display(language_code, LANGUAGE_LABELS.get(language_code)),
                "type": doc_type,
                "url": entry.get("id"),
                "abstract": entry.get("summary"),
            }


registry.register("arxiv", ArxivSource)
