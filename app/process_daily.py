from datetime import datetime

from app.db import (
    get_articles_for_daily_processing,
    init_db,
)
from app.digest import get_processing_lookback_hours
from app.log import log_event
from app.processor import process_articles


def process_daily() -> dict:
    init_db()

    lookback_hours = get_processing_lookback_hours()

    articles = get_articles_for_daily_processing(
        lookback_hours=lookback_hours,
    )

    print()
    print(f"Daily processing window: last {lookback_hours} hours")
    print(f"Eligible unprocessed articles: {len(articles)}")

    summary = process_articles(articles)
    summary["eligible"] = len(articles)

    print()
    print("Daily processing complete.")
    print(f"Processed successfully: {summary['processed']}")
    print(f"Failed/retrying: {summary['failed']}")
    log_event(
        "process_daily",
        eligible=len(articles),
        processed=summary["processed"],
        failed=summary["failed"],
    )

    return summary


def run_process_job() -> dict:
    started_at = datetime.now().astimezone()

    print()
    print("=" * 60)
    print(f"Daily classification started at: {started_at.isoformat(timespec='seconds')}")
    print("=" * 60)

    summary = process_daily()

    finished_at = datetime.now().astimezone()

    print()
    print("=" * 60)
    print(f"Daily classification finished at: {finished_at.isoformat(timespec='seconds')}")
    print("=" * 60)

    return summary


def main():
    from app.pipeline import run_cli

    run_cli("process")


if __name__ == "__main__":
    main()
