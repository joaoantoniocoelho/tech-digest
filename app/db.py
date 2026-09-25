import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import (
    datetime,
    timedelta,
    timezone,
)
from email.utils import parsedate_to_datetime
from pathlib import Path


def _resolve_db_path() -> Path:
    configured = os.getenv(
        "DIGEST_DB_PATH",
        "",
    ).strip()

    if configured:
        return Path(configured)

    return Path("data/digest.db")


DB_PATH = _resolve_db_path()


@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = (
        sqlite3.Row
    )

    connection.execute(
        "PRAGMA journal_mode=WAL"
    )
    connection.execute(
        "PRAGMA busy_timeout=30000"
    )
    connection.execute(
        "PRAGMA foreign_keys=ON"
    )
    connection.commit()

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                published_at TEXT,
                discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                relevance_score INTEGER,
                why_interesting TEXT,
                topics TEXT,
                processed_at TEXT,
                feed_excerpt TEXT,
                processing_attempts INTEGER NOT NULL DEFAULT 0,
                last_processing_error TEXT,
                failed_at TEXT,
                delivered_at TEXT
            )
            """
        )

        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(articles)"
            )
        }

        migrations = {
            "relevance_score":
                "ALTER TABLE articles ADD COLUMN relevance_score INTEGER",

            "why_interesting":
                "ALTER TABLE articles ADD COLUMN why_interesting TEXT",

            "topics":
                "ALTER TABLE articles ADD COLUMN topics TEXT",

            "processed_at":
                "ALTER TABLE articles ADD COLUMN processed_at TEXT",

            "feed_excerpt":
                "ALTER TABLE articles ADD COLUMN feed_excerpt TEXT",

            "processing_attempts":
                """
                ALTER TABLE articles
                ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0
                """,

            "last_processing_error":
                """
                ALTER TABLE articles
                ADD COLUMN last_processing_error TEXT
                """,

            "failed_at":
                "ALTER TABLE articles ADD COLUMN failed_at TEXT",

            "delivered_at":
                "ALTER TABLE articles ADD COLUMN delivered_at TEXT",
        }

        for column, migration in migrations.items():
            if column not in columns:
                connection.execute(
                    migration
                )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                subscribed_at TEXT NOT NULL,
                unsubscribed_at TEXT,
                unsubscribe_token TEXT NOT NULL UNIQUE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_lock (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                job TEXT NOT NULL,
                owner TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS job_runs (
                slot TEXT PRIMARY KEY,
                job TEXT NOT NULL,
                status TEXT NOT NULL,
                finished_at TEXT NOT NULL
            )
            """
        )


def save_article(article: dict) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO articles (
                source,
                title,
                url,
                published_at,
                feed_excerpt
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                article["source"],
                article["title"],
                article["url"],
                article["published_at"],
                article.get(
                    "feed_excerpt"
                ),
            ),
        )

        is_new = cursor.rowcount == 1

        feed_excerpt = article.get(
            "feed_excerpt"
        )

        if feed_excerpt:
            connection.execute(
                """
                UPDATE articles
                SET feed_excerpt = ?
                WHERE url = ?
                  AND (
                    feed_excerpt IS NULL
                    OR feed_excerpt = ''
                  )
                """,
                (
                    feed_excerpt,
                    article["url"],
                ),
            )

        return is_new


def _parse_article_datetime(
    value: str | None,
):
    if not value:
        return None

    value = value.strip()

    try:
        parsed = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(
            value
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        return None


def article_datetime(
    published_at: str | None,
    discovered_at: str | None,
):
    return (
        _parse_article_datetime(
            published_at
        )
        or _parse_article_datetime(
            discovered_at
        )
    )


def get_articles_for_daily_processing(
    lookback_hours: int,
):
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(
            hours=lookback_hours
        )
    )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                source,
                title,
                url,
                published_at,
                discovered_at,
                feed_excerpt,
                processing_attempts
            FROM articles
            WHERE processed_at IS NULL
              AND failed_at IS NULL
            """
        ).fetchall()

    articles = []

    for row in rows:
        attempts = row[
            "processing_attempts"
        ]
        published_time = article_datetime(
            row["published_at"],
            row["discovered_at"],
        )

        in_window = (
            published_time is not None
            and published_time >= cutoff
        )
        in_flight_retry = attempts > 0

        if not in_window and not in_flight_retry:
            continue

        articles.append(
            {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "url": row["url"],
                "published_at": row[
                    "published_at"
                ],
                "discovered_at": row[
                    "discovered_at"
                ],
                "feed_excerpt": row[
                    "feed_excerpt"
                ],
                "processing_attempts": (
                    attempts
                ),
                "article_datetime": (
                    published_time
                ),
            }
        )

    articles.sort(
        key=lambda article: (
            article["article_datetime"]
            or datetime.min.replace(
                tzinfo=timezone.utc
            ),
            article["id"],
        )
    )

    return articles


def _article_from_row(row):
    if row is None:
        return None

    return {
        "id": row["id"],
        "source": row["source"],
        "title": row["title"],
        "url": row["url"],
        "published_at": row["published_at"],
        "discovered_at": row["discovered_at"],
        "feed_excerpt": row["feed_excerpt"],
        "processing_attempts": row[
            "processing_attempts"
        ],
    }


def get_article_by_id(article_id: int):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                source,
                title,
                url,
                published_at,
                discovered_at,
                feed_excerpt,
                processing_attempts
            FROM articles
            WHERE id = ?
            """,
            (article_id,),
        ).fetchone()

    return _article_from_row(row)


def get_article_by_url(url: str):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                source,
                title,
                url,
                published_at,
                discovered_at,
                feed_excerpt,
                processing_attempts
            FROM articles
            WHERE url = ?
            """,
            (url,),
        ).fetchone()

    return _article_from_row(row)


def save_classification(
    article_id: int,
    result: dict,
):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE articles
            SET relevance_score = ?,
                why_interesting = ?,
                topics = ?,
                processed_at = CURRENT_TIMESTAMP,
                last_processing_error = NULL,
                failed_at = NULL
            WHERE id = ?
            """,
            (
                result[
                    "relevance_score"
                ],
                result[
                    "why_interesting"
                ],
                json.dumps(
                    result["topics"],
                    ensure_ascii=False,
                ),
                article_id,
            ),
        )


def record_processing_error(
    article_id: int,
    error: str,
    fail_after_attempts: int | None = None,
):
    error = error[:1000]

    with get_connection() as connection:
        connection.execute(
            """
            UPDATE articles
            SET processing_attempts = processing_attempts + 1,
                last_processing_error = ?
            WHERE id = ?
            """,
            (
                error,
                article_id,
            ),
        )

        row = connection.execute(
            """
            SELECT
                processing_attempts,
                failed_at
            FROM articles
            WHERE id = ?
            """,
            (article_id,),
        ).fetchone()

        if (
            fail_after_attempts
            is not None
            and row[
                "processing_attempts"
            ]
            >= fail_after_attempts
            and row["failed_at"]
            is None
        ):
            connection.execute(
                """
                UPDATE articles
                SET failed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (article_id,),
            )

            row = connection.execute(
                """
                SELECT
                    processing_attempts,
                    failed_at
                FROM articles
                WHERE id = ?
                """,
                (article_id,),
            ).fetchone()

        return row


def get_digest_candidates(
    lookback_hours: int,
    minimum_score: int,
    maximum_articles: int | None = None,
):
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(
            hours=lookback_hours
        )
    )

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                source,
                title,
                url,
                published_at,
                feed_excerpt,
                relevance_score,
                why_interesting,
                topics,
                discovered_at,
                processed_at,
                delivered_at
            FROM articles
            WHERE processed_at IS NOT NULL
              AND failed_at IS NULL
              AND delivered_at IS NULL
              AND relevance_score >= ?
            ORDER BY
                relevance_score DESC,
                discovered_at DESC,
                id DESC
            """,
            (
                minimum_score,
            ),
        ).fetchall()

    articles = []

    for row in rows:
        candidate_datetime = article_datetime(
            row["published_at"],
            row["discovered_at"],
        )

        if (
            candidate_datetime is None
            or candidate_datetime < cutoff
        ):
            continue

        topics = []

        if row["topics"]:
            try:
                topics = json.loads(
                    row["topics"]
                )
            except json.JSONDecodeError:
                topics = []

        articles.append(
            {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "url": row["url"],
                "published_at": (
                    row["published_at"]
                ),
                "feed_excerpt": row["feed_excerpt"],
                "relevance_score": (
                    row[
                        "relevance_score"
                    ]
                ),
                "why_interesting": (
                    row[
                        "why_interesting"
                    ]
                ),
                "topics": topics,
                "discovered_at": (
                    row[
                        "discovered_at"
                    ]
                ),
                "processed_at": (
                    row[
                        "processed_at"
                    ]
                ),
                "delivered_at": (
                    row[
                        "delivered_at"
                    ]
                ),
            }
        )

        if (
            maximum_articles is not None
            and len(articles) >= maximum_articles
        ):
            break

    return articles


def mark_articles_delivered(
    article_ids: list[int],
):
    if not article_ids:
        return

    placeholders = ",".join(
        "?"
        for _ in article_ids
    )

    with get_connection() as connection:
        connection.execute(
            f"""
            UPDATE articles
            SET delivered_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
              AND delivered_at IS NULL
            """,
            article_ids,
        )


def get_latest_edition() -> dict | None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                source,
                title,
                url,
                published_at,
                relevance_score,
                why_interesting,
                topics,
                delivered_at
            FROM articles
            WHERE delivered_at = (
                SELECT MAX(delivered_at)
                FROM articles
            )
            ORDER BY
                relevance_score DESC,
                discovered_at DESC,
                id DESC
            """
        ).fetchall()

    if not rows:
        return None

    articles = []

    for row in rows:
        try:
            topics = json.loads(row["topics"] or "[]")
        except json.JSONDecodeError:
            topics = []

        articles.append(
            {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"],
                "relevance_score": row["relevance_score"],
                "why_interesting": row["why_interesting"],
                "topics": topics,
            }
        )

    return {
        "delivered_at": _parse_article_datetime(
            rows[0]["delivered_at"]
        ),
        "articles": articles,
    }
