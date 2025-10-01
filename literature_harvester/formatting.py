"""Utilities for rendering bibliography entries."""
from __future__ import annotations

from typing import Dict, List


def chicago_entry(record: Dict) -> str:
    authors = record.get("authors") or []
    author_part = "; ".join(authors) if authors else "Unknown"
    year = record.get("year") or "n.d."
    title = record.get("title") or "Untitled"
    publisher = record.get("publisher") or record.get("source") or ""
    doi = record.get("doi")
    url = record.get("url")
    parts: List[str] = [f"{author_part}. {year}. \"{title}.\""]
    if publisher:
        parts.append(publisher)
    if doi:
        parts.append(f"https://doi.org/{doi}")
    elif url:
        parts.append(url)
    return " ".join(parts)


def chicago_bibliography(records: List[Dict]) -> str:
    return "\n".join(chicago_entry(record) for record in records)
