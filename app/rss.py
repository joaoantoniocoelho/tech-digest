from html.parser import HTMLParser

import feedparser
import httpx


MAX_FEED_EXCERPT_LENGTH = 2500
FEED_TIMEOUT_SECONDS = 15.0
_UNSET = object()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        text = data.strip()

        if text:
            self.parts.append(text)

    def get_text(self):
        return " ".join(self.parts)


def _html_to_text(value: str) -> str:
    parser = _HTMLTextExtractor()

    try:
        parser.feed(value)
        text = parser.get_text()
    except Exception:
        text = value

    return " ".join(text.split())


def _get_feed_excerpt(entry) -> str | None:
    candidates = []

    summary = entry.get("summary")

    if summary:
        candidates.append(summary)

    content = entry.get("content")

    if content:
        for item in content:
            value = item.get("value")

            if value:
                candidates.append(value)

    for candidate in candidates:
        text = _html_to_text(candidate)

        if text:
            return text[:MAX_FEED_EXCERPT_LENGTH]

    return None


def _download_feed(name: str, url: str) -> bytes | None:
    try:
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=FEED_TIMEOUT_SECONDS,
            headers={
                "User-Agent": (
                    "tech-digest/0.1 "
                    "(https://github.com/joaoantoniocoelho/tech-digest)"
                )
            },
        )

        response.raise_for_status()

        return response.content

    except httpx.TimeoutException:
        print(
            f"[WARN] Feed request timed out after "
            f"{FEED_TIMEOUT_SECONDS:.0f}s: {name}"
        )

    except httpx.HTTPStatusError as error:
        print(
            f"[WARN] Feed returned HTTP "
            f"{error.response.status_code}: {name}"
        )

    except httpx.HTTPError as error:
        print(
            f"[WARN] Failed to download feed "
            f"{name}: {error}"
        )

    return None


def fetch_feed(name: str, url: str, max_entries=_UNSET):
    if max_entries is not _UNSET and (
        isinstance(max_entries, bool)
        or not isinstance(max_entries, int)
        or max_entries <= 0
    ):
        raise ValueError("max_entries must be a positive integer")

    feed_content = _download_feed(
        name=name,
        url=url,
    )

    if feed_content is None:
        return []

    feed = feedparser.parse(feed_content)

    if feed.bozo and not feed.entries:
        print(f"[WARN] Failed to parse feed: {name}")
        return []

    if feed.bozo:
        print(
            f"[WARN] Feed reported a parsing issue "
            f"but returned entries: {name}"
        )

    articles = []

    entries = feed.entries if max_entries is _UNSET else feed.entries[:max_entries]

    for entry in entries:
        article_url = entry.get("link")

        if not article_url:
            continue

        articles.append(
            {
                "source": name,
                "title": entry.get(
                    "title",
                    "Untitled",
                ),
                "url": article_url,
                "published_at": (
                    entry.get("published")
                    or entry.get("updated")
                ),
                "feed_excerpt": _get_feed_excerpt(entry),
            }
        )

    return articles
