"""Crossref search source."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Sequence, Set

import requests

from .base import Source, registry
from .utils import language_display, normalize_language_code


def _tokenize_keywords(raw: str | None) -> List[str]:
    if not raw:
        return []
    normalized = raw.replace("，", " ")
    return [part.lower() for part in normalized.split() if part]


def _text_matches(terms: Sequence[str], candidates: Sequence[str]) -> bool:
    if not terms:
        return True
    haystack = " ".join(
        re.sub(r"<[^>]+>", " ", text).lower()
        for text in candidates
        if isinstance(text, str)
    )
    return all(term in haystack for term in terms)


def _collect_candidates(item: Dict, mode: str) -> List[str]:
    raw_title = item.get("title")
    if isinstance(raw_title, list):
        title_values = list(raw_title)
    elif isinstance(raw_title, str):
        title_values = [raw_title]
    else:
        title_values = []

    raw_abstract = item.get("abstract")
    abstract = raw_abstract if isinstance(raw_abstract, str) else ""

    raw_subjects = item.get("subject")
    if isinstance(raw_subjects, list):
        subjects = list(raw_subjects)
    elif isinstance(raw_subjects, str):
        subjects = [raw_subjects]
    else:
        subjects = []

    raw_container = item.get("container-title")
    if isinstance(raw_container, list):
        container = list(raw_container)
    elif isinstance(raw_container, str):
        container = [raw_container]
    else:
        container = []

    raw_subtitle = item.get("subtitle")
    if isinstance(raw_subtitle, list):
        subtitle = list(raw_subtitle)
    elif isinstance(raw_subtitle, str):
        subtitle = [raw_subtitle]
    else:
        subtitle = []

    if mode == "title":
        return list(title_values)
    if mode == "keywords":
        candidates = list(subjects)
        if abstract:
            candidates.append(abstract)
        return candidates
    candidates = list(title_values)
    candidates.extend(container)
    candidates.extend(subtitle)
    if abstract:
        candidates.append(abstract)
    candidates.extend(subjects)
    return candidates


class CrossrefSource(Source):
    API_URL = "https://api.crossref.org/works"

    def search(self, query: Dict) -> Iterable[Dict]:
        params = {
            "rows": self.config.get("rows", 50),
        }
        keywords = query.get("keywords") or ""
        search_mode = (query.get("search_mode") or "full_text").lower()
        if search_mode not in {"full_text", "keywords", "title"}:
            search_mode = "full_text"
        if keywords:
            if search_mode == "title":
                params["query.title"] = keywords
            elif search_mode == "keywords":
                params["query"] = keywords
            else:
                params["query"] = keywords
        filters: List[str] = []
        if query.get("start_date"):
            filters.append(f"from-pub-date:{query['start_date']}")
        if query.get("end_date"):
            filters.append(f"until-pub-date:{query['end_date']}")
        if query.get("authors"):
            params["query.author"] = " ".join(query["authors"])
        if filters:
            params["filter"] = ",".join(filters)
        requested_languages: Set[str] = {
            lang
            for lang in (
                normalize_language_code(language)
                for language in query.get("languages", [])
            )
            if lang
        }
        response = requests.get(self.API_URL, params=params, timeout=30)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
        keyword_terms = _tokenize_keywords(keywords)
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
            language_raw = item.get("language") or ""
            language_code = normalize_language_code(language_raw)
            if requested_languages and language_code not in requested_languages:
                continue
            doc_type = item.get("type") or ""
            if query.get("formats") and doc_type not in query["formats"]:
                continue
            if keywords:
                candidates = _collect_candidates(item, search_mode)
                if not _text_matches(keyword_terms, candidates):
                    continue
            url = item.get("URL")
            yield {
                "source": self.name,
                "title": title[0] if title else "",
                "authors": authors,
                "doi": item.get("DOI"),
                "publisher": item.get("publisher"),
                "year": year,
                "language": language_code or language_raw,
                "language_display": language_display(language_code, language_raw),
                "type": doc_type,
                "url": url,
                "abstract": item.get("abstract"),
            }


registry.register("crossref", CrossrefSource)
