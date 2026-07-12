"""Text matching helpers for the BOM filter."""

import re
from collections.abc import Callable, Iterable
from typing import TypeVar


T = TypeVar("T")


def split_bom_filter_terms(value: object) -> list[str]:
    """Return the non-empty, comma-separated filter terms."""
    return [term.strip() for term in str(value or "").split(",") if term.strip()]


def matches_bom_filter_text(haystack: object, query: object, *, whole_word: bool = False) -> bool:
    """Match any comma-separated query term against BOM text, case-insensitively."""
    terms = split_bom_filter_terms(query)
    if not terms:
        return True

    text = str(haystack or "").casefold()
    if not whole_word:
        return any(term.casefold() in text for term in terms)

    return any(
        re.search(rf"(?<!\w){re.escape(term.casefold())}(?!\w)", text) is not None
        for term in terms
    )


def deduplicate_bom_items_by_id(items: Iterable[T], bom_id_getter: Callable[[T], object]) -> list[T]:
    """Keep the first item for each BOM ID while preserving input order."""
    unique_items = []
    seen_ids = set()
    for item in items:
        bom_id = bom_id_getter(item)
        if bom_id is None:
            unique_items.append(item)
            continue
        try:
            if bom_id in seen_ids:
                continue
            seen_ids.add(bom_id)
        except TypeError:
            # An invalid/unhashable identity cannot be safely treated as a duplicate.
            pass
        unique_items.append(item)
    return unique_items
