from datetime import datetime

from app.db import (
    get_articles_for_daily_processing,
    init_db,
)
from app.digest import get_processing_lookback_hours
from app.processor import process_articles


def process_daily() -> dict:
    init_db()

    lookback_hours = (
        get_processing_lookback_hours()
    )

    articles = get_articles_for_daily_processing(
        lookback_hours=lookback_hours,
    )

    print()
    print(
        "Daily processing window: "
        f"last {lookback_hours} hours"
    )
    print(
        "Eligible unprocessed articles: "
        f"{len(articles)}"
    )

    summary = process_articles(articles)

    print()
    print("Daily processing complete.")
    print(
        "Processed successfully: "
        f"{summary['processed']}"
    )
    print(
        "Failed/retrying: "
        f"{summary['failed']}"
    )

    return summary


def main():
    started_at = datetime.now().astimezone()

    print()
    print("=" * 60)
    print(
        "Daily classification started at: "
        f"{started_at.isoformat(timespec='seconds')}"
    )
    print("=" * 60)

    process_daily()

    finished_at = datetime.now().astimezone()

    print()
    print("=" * 60)
    print(
        "Daily classification finished at: "
        f"{finished_at.isoformat(timespec='seconds')}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()
