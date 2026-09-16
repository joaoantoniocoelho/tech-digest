from pathlib import Path

import yaml

from datetime import datetime
from app.db import init_db, save_article
from app.rss import fetch_feed


def main():
    started_at = datetime.now().astimezone()

    print()
    print("=" * 60)
    print(f"Run started at: {started_at.isoformat(timespec='seconds')}")
    print("=" * 60)

    init_db()

    config_path = Path("config/sources.yaml")

    with config_path.open() as file:
        config = yaml.safe_load(file)

    total_new = 0

    for source in config["sources"]:
        print(f"\nChecking {source['name']}...")

        articles = fetch_feed(
            name=source["name"],
            url=source["url"],
        )

        source_new = 0

        for article in articles:
            if not article["url"]:
                continue

            if save_article(article):
                source_new += 1
                total_new += 1

        print(f"{source_new} new articles")

    finished_at = datetime.now().astimezone()
    print()
    print(f"Total: {total_new} new articles")
    print(f"Run finished at: {finished_at.isoformat(timespec='seconds')}")

if __name__ == "__main__":
    main()
