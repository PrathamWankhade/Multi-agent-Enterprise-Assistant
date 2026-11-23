"""Lightweight lexical (keyword) retrieval over bundled markdown documents.

Used as a fallback when real embeddings are unavailable (e.g. no embedding
model is pulled) so the Research Agent still returns grounded answers from
the local knowledge base.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "for", "and", "or", "but", "with", "as", "at",
    "by", "from", "that", "this", "these", "those", "it", "its", "was",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "do", "does", "did", "can", "could", "should", "would", "will", "i",
    "you", "we", "they", "he", "she", "me", "my", "our", "your", "their",
    "about", "into", "than", "then", "there", "here", "have", "has", "had",
    "please", "tell", "give", "show", "help", "any", "some", "all",
}


def _tokens(text: str) -> List[str]:
    return [
        t
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def _split_sections(text: str) -> List[str]:
    """Split a markdown document into sections by headings."""
    sections: List[str] = []
    current: List[str] = []

    def flush() -> None:
        if current:
            section = "\n".join(current).strip()
            if section:
                sections.append(section)
            current.clear()

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            current.append(line.lstrip("#").strip())
        else:
            current.append(line)
    flush()
    return sections


def _load_sections() -> List[Tuple[str, str]]:
    """Return (source, section_text) pairs from markdown files in data/."""
    docs: List[Tuple[str, str]] = []
    if not DATA_DIR.exists():
        return docs
    for path in sorted(DATA_DIR.glob("*.md")):
        if path.name.upper() == "README.MD":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            logger.warning("Could not read %s: %s", path, exc)
            continue
        for section in _split_sections(text):
            docs.append((path.name, section))
    return docs


def keyword_search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Score sections by keyword overlap and return the best matches."""
    query_terms = _tokens(query)
    if not query_terms:
        return []

    query_set = set(query_terms)
    scored: List[Tuple[float, str, str]] = []

    for source, section in _load_sections():
        section_terms = _tokens(section)
        if not section_terms:
            continue
        section_set = set(section_terms)
        overlap = len(query_set & section_set)
        if overlap == 0:
            continue
        # Normalise by sqrt of section length to avoid favoring huge sections
        score = overlap / (len(section_set) ** 0.5)
        # Bonus for exact phrase presence
        if query.lower() in section.lower():
            score += 2.0
        scored.append((score, source, section))

    scored.sort(key=lambda item: item[0], reverse=True)

    results: List[Dict[str, Any]] = []
    top_score = scored[0][0] if scored else 1.0
    for rank, (score, source, section) in enumerate(scored[:top_k]):
        results.append(
            {
                "chunk_id": f"lexical-{rank}",
                "text": section,
                "source": source,
                "doc_id": source,
                "page": None,
                "section": section.splitlines()[0][:80] if section else "",
                "metadata": {"retrieval": "keyword"},
                "score": round(score / top_score, 3) if top_score else 0.0,
            }
        )
    return results
