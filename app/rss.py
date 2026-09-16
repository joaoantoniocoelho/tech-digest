import feedparser


def fetch_feed(name: str, url: str):
    feed = feedparser.parse(url)

    if feed.bozo:
        print(f"[WARN] Failed to parse feed: {name}")
        return []

    articles = []

    for entry in feed.entries:
        articles.append(
            {
                "source": name,
                "title": entry.get("title", "Untitled"),
                "url": entry.get("link"),
                "published_at": entry.get("published"),
            }
        )

    return articles
