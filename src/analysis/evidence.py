"""Evidence grounding helpers — verify LLM quotes against source text.

Both the paper enricher and the conflict detector require quotes to be
verbatim spans of the source material. Matching is deliberately lenient about
whitespace, case, and punctuation (models reformat hyphens and spacing) but
strict about content: a quote that cannot be found in the source is treated as
hallucinated and the carrying field is dropped.
"""

import re

MIN_QUOTE_CHARS = 12
"""Minimum normalized length for a quote to count as evidence."""

_MATCH_NORMALIZER = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def normalize_for_match(text: str) -> str:
    """Lowercase and collapse everything that is not alphanumeric/CJK."""
    return re.sub(r"\s+", " ", _MATCH_NORMALIZER.sub(" ", (text or "").lower())).strip()


def source_text_of(paper: dict) -> str:
    """The evidence base text for a paper: title + abstract."""
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    return f"{title}. {abstract}".strip()


def verify_quote(quote: str, source_text: str) -> bool:
    """True when ``quote`` is a (normalized) substring of ``source_text``."""
    normalized = normalize_for_match(quote)
    return len(normalized) >= MIN_QUOTE_CHARS and normalized in normalize_for_match(source_text)
