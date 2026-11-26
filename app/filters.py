import re
from datetime import datetime

LAUNCH_PREFIXES = ("show hn:", "launch hn:")

_TRAILING_YEAR = re.compile(r"\((\d{4})\)\s*$")


def _normalize(title: str) -> str:
    return " ".join(title.split()).casefold()


def _contains_phrase(title: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase.casefold())}\b", title) is not None


def _contains_word_start(title: str, word: str) -> bool:
    # Prefix match catches inflections ("-ing", "-ed") while the leading
    # boundary keeps innocent words that merely contain the letters.
    return re.search(rf"\b{re.escape(word.casefold())}", title) is not None


def _is_dated(title: str, current_year: int) -> bool:
    # Aggregators tag resurfaced posts with their original year, e.g. "(2004)".
    match = _TRAILING_YEAR.search(title)

    return match is not None and int(match.group(1)) < current_year


def is_blocked_title(
    title: str,
    filters: dict,
    current_year: int | None = None,
) -> bool:
    """Whether a title can never be part of an edition."""
    normalized = _normalize(title)

    if any(_contains_phrase(normalized, phrase) for phrase in filters.get("blocked_phrases", [])):
        return True

    if any(_contains_word_start(normalized, word) for word in filters.get("blocked_words", [])):
        return True

    if not filters.get("block_dated_titles"):
        return False

    return _is_dated(normalized, current_year or datetime.now().year)


def is_launch_post(title: str) -> bool:
    return _normalize(title).startswith(LAUNCH_PREFIXES)
