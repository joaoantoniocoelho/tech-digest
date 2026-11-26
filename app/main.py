from datetime import datetime
from pathlib import Path

import yaml

from app.db import init_db, save_article
from app.digest import load_digest_config
from app.filters import is_blocked_title
from app.log import log_event
from app.rss import fetch_feed


def collect_feeds() -> int:
    config_path = Path("config/sources.yaml")

    with config_path.open() as file:
        config = yaml.safe_load(file)

    title_filters = load_digest_config().get("title_filters") or {}

    total_new = 0

    print()
    print("Collecting feeds...")

    for source in config["sources"]:
        print()
        print(f"Collecting: {source['name']}")

        feed_options = {}
        if "max_entries" in source:
            feed_options["max_entries"] = source["max_entries"]
        articles = fetch_feed(name=source["name"], url=source["url"], **feed_options)

        print(f"Found: {len(articles)} entries")

        source_new = 0

        for article in articles:
            if not article["url"]:
                continue

            # Never deliverable, so not worth a classification call.
            if is_blocked_title(article["title"], title_filters):
                continue

            if save_article(article):
                source_new += 1
                total_new += 1

        print(f"New: {source_new}")

    print()
    print(f"Collection complete: {total_new} new articles")
    log_event("collect_feeds", new_articles=total_new)

    return total_new


def run_collect() -> dict:
    started_at = datetime.now().astimezone()

    print()
    print("=" * 60)
    print(f"Run started at: {started_at.isoformat(timespec='seconds')}")
    print("=" * 60)

    init_db()
    total_new = collect_feeds()

    finished_at = datetime.now().astimezone()

    print()
    print("=" * 60)
    print(f"Run finished at: {finished_at.isoformat(timespec='seconds')}")
    print("=" * 60)

    return {"new_articles": total_new}


def main():
    from app.pipeline import run_cli

    run_cli("collect")


if __name__ == "__main__":
    main()
