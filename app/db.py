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

SUBSCRIBER_ATTRIBUTION_COLUMNS = (
    "acquisition_source",
    "acquisition_url",
    "utm_source",
    "utm_medium",
    "utm_campaign",
)


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

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
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

        columns = {row["name"] for row in connection.execute("PRAGMA table_info(articles)")}

        migrations = {
            "relevance_score": "ALTER TABLE articles ADD COLUMN relevance_score INTEGER",
            "why_interesting": "ALTER TABLE articles ADD COLUMN why_interesting TEXT",
            "topics": "ALTER TABLE articles ADD COLUMN topics TEXT",
            "processed_at": "ALTER TABLE articles ADD COLUMN processed_at TEXT",
            "feed_excerpt": "ALTER TABLE articles ADD COLUMN feed_excerpt TEXT",
            "processing_attempts": """
                ALTER TABLE articles
                ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0
                """,
            "last_processing_error": """
                ALTER TABLE articles
                ADD COLUMN last_processing_error TEXT
                """,
            "failed_at": "ALTER TABLE articles ADD COLUMN failed_at TEXT",
            "delivered_at": "ALTER TABLE articles ADD COLUMN delivered_at TEXT",
            "importance": "ALTER TABLE articles ADD COLUMN importance INTEGER",
            "feature_strengths": "ALTER TABLE articles ADD COLUMN feature_strengths TEXT",
        }

        for column, migration in migrations.items():
            if column not in columns:
                connection.execute(migration)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                subscribed_at TEXT NOT NULL,
                unsubscribed_at TEXT,
                unsubscribe_token TEXT NOT NULL UNIQUE,
                created_at TEXT,
                acquisition_source TEXT,
                acquisition_url TEXT,
                utm_source TEXT,
                utm_medium TEXT,
                utm_campaign TEXT
            )
            """
        )

        subscriber_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(subscribers)")
        }

        for column in ("created_at", *SUBSCRIBER_ATTRIBUTION_COLUMNS):
            if column not in subscriber_columns:
                connection.execute(f"ALTER TABLE subscribers ADD COLUMN {column} TEXT")

        # Rows from before created_at existed: subscribed_at is the best
        # available signup date, although a past reactivation may have reset it.
        connection.execute(
            """
            UPDATE subscribers
            SET created_at = subscribed_at
            WHERE created_at IS NULL
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

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS author_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                intro TEXT NOT NULL,
                published_at TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                approved_at TEXT,
                edition_date TEXT,
                sent_at TEXT
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
                article.get("feed_excerpt"),
            ),
        )

        is_new = cursor.rowcount == 1

        feed_excerpt = article.get("feed_excerpt")

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
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except ValueError:
        pass

    try:
        parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

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
    return _parse_article_datetime(published_at) or _parse_article_datetime(discovered_at)


def get_articles_for_daily_processing(
    lookback_hours: int,
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

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
        attempts = row["processing_attempts"]
        published_time = article_datetime(
            row["published_at"],
            row["discovered_at"],
        )

        in_window = published_time is not None and published_time >= cutoff
        in_flight_retry = attempts > 0

        if not in_window and not in_flight_retry:
            continue

        articles.append(
            {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"],
                "discovered_at": row["discovered_at"],
                "feed_excerpt": row["feed_excerpt"],
                "processing_attempts": (attempts),
                "article_datetime": (published_time),
            }
        )

    articles.sort(
        key=lambda article: (
            article["article_datetime"] or datetime.min.replace(tzinfo=timezone.utc),
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
        "processing_attempts": row["processing_attempts"],
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
                importance = ?,
                feature_strengths = ?,
                processed_at = CURRENT_TIMESTAMP,
                last_processing_error = NULL,
                failed_at = NULL
            WHERE id = ?
            """,
            (
                result["relevance_score"],
                result["why_interesting"],
                json.dumps(
                    result["topics"],
                    ensure_ascii=False,
                ),
                result.get("importance"),
                _strengths_json(result.get("feature_strengths")),
                article_id,
            ),
        )


def _strengths_json(feature_strengths: dict | None) -> str | None:
    if feature_strengths is None:
        return None

    return json.dumps(feature_strengths, sort_keys=True)


def get_scored_articles(days: int) -> list[dict]:
    """Classifier answers stored in the last `days` days, for offline rescoring."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT source, title, relevance_score, importance,
                   feature_strengths, processed_at, delivered_at
            FROM articles
            WHERE processed_at >= ?
              AND failed_at IS NULL
              AND feature_strengths IS NOT NULL
              AND importance IS NOT NULL
            ORDER BY processed_at, id
            """,
            (cutoff,),
        ).fetchall()

    return [
        {
            "source": row["source"],
            "title": row["title"],
            "relevance_score": row["relevance_score"],
            "importance": row["importance"],
            "feature_strengths": json.loads(row["feature_strengths"]),
            "day": row["processed_at"][:10],
            "delivered": row["delivered_at"] is not None,
        }
        for row in rows
    ]


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
            fail_after_attempts is not None
            and row["processing_attempts"] >= fail_after_attempts
            and row["failed_at"] is None
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
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

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
            (minimum_score,),
        ).fetchall()

    articles = []

    for row in rows:
        candidate_datetime = article_datetime(
            row["published_at"],
            row["discovered_at"],
        )

        if candidate_datetime is None or candidate_datetime < cutoff:
            continue

        topics = []

        if row["topics"]:
            try:
                topics = json.loads(row["topics"])
            except json.JSONDecodeError:
                topics = []

        articles.append(
            {
                "id": row["id"],
                "source": row["source"],
                "title": row["title"],
                "url": row["url"],
                "published_at": (row["published_at"]),
                "feed_excerpt": row["feed_excerpt"],
                "relevance_score": (row["relevance_score"]),
                "why_interesting": (row["why_interesting"]),
                "topics": topics,
                "discovered_at": (row["discovered_at"]),
                "processed_at": (row["processed_at"]),
                "delivered_at": (row["delivered_at"]),
            }
        )

        if maximum_articles is not None and len(articles) >= maximum_articles:
            break

    return articles


def get_recently_delivered(days: int) -> list[dict]:
    """Stories delivered in the last `days` days, for cross-day deduplication."""
    # Same UTC format mark_articles_delivered writes, so strings compare.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT title, feed_excerpt
            FROM articles
            WHERE delivered_at >= ?
            ORDER BY delivered_at DESC, id DESC
            """,
            (cutoff,),
        ).fetchall()

    return [{"title": row["title"], "feed_excerpt": row["feed_excerpt"]} for row in rows]


def mark_articles_delivered(
    article_ids: list[int],
    delivered_at: datetime | None = None,
):
    if not article_ids:
        return

    placeholders = ",".join("?" for _ in article_ids)
    # Same UTC format as CURRENT_TIMESTAMP, so string MAX() and date() keep
    # working; an explicit send time keeps the edition date the email links to.
    timestamp = (
        (delivered_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%d %H:%M:%S")
    )

    with get_connection() as connection:
        connection.execute(
            f"""
            UPDATE articles
            SET delivered_at = ?
            WHERE id IN ({placeholders})
              AND delivered_at IS NULL
            """,
            [timestamp, *article_ids],
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
        "delivered_at": _parse_article_datetime(rows[0]["delivered_at"]),
        "articles": articles,
    }


def list_public_editions(limit: int, offset: int = 0) -> list[dict[str, str | int]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT date(delivered_at) AS edition_date,
                   COUNT(*) AS article_count
            FROM articles
            WHERE delivered_at IS NOT NULL
            GROUP BY date(delivered_at)
            ORDER BY edition_date DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    return [{"date": row["edition_date"], "article_count": row["article_count"]} for row in rows]


def get_public_edition(edition_date: str) -> dict | None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT source, title, url, published_at,
                   why_interesting, topics
            FROM articles
            WHERE date(delivered_at) = ?
            ORDER BY relevance_score DESC, discovered_at DESC, id DESC
            """,
            (edition_date,),
        ).fetchall()
        if not rows:
            return None
        neighbors = connection.execute(
            """
            SELECT
                (SELECT MAX(date(delivered_at)) FROM articles
                 WHERE delivered_at IS NOT NULL
                   AND date(delivered_at) < ?) AS older_date,
                (SELECT MIN(date(delivered_at)) FROM articles
                 WHERE delivered_at IS NOT NULL
                   AND date(delivered_at) > ?) AS newer_date
            """,
            (edition_date, edition_date),
        ).fetchone()

    articles = []
    for row in rows:
        try:
            topics = json.loads(row["topics"] or "[]")
        except json.JSONDecodeError:
            topics = []
        articles.append(
            {
                "title": row["title"],
                "url": row["url"],
                "source": row["source"],
                "published_at": row["published_at"],
                "why_interesting": row["why_interesting"],
                "topics": topics,
            }
        )

    return {
        "date": edition_date,
        "articles": articles,
        "note": get_edition_note(edition_date),
        "older_date": neighbors["older_date"],
        "newer_date": neighbors["newer_date"],
    }


_NOTE_COLUMNS = (
    "id, slug, title, url, intro, published_at, status, "
    "created_at, updated_at, approved_at, edition_date, sent_at"
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_notes() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT {_NOTE_COLUMNS}
            FROM author_notes
            ORDER BY id DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_note(slug: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT {_NOTE_COLUMNS}
            FROM author_notes
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()

    return dict(row) if row else None


def save_note_draft(note: dict) -> str:
    """Create or update a note as a draft. Returns created, updated or sent."""
    now = _utc_timestamp()

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT status
            FROM author_notes
            WHERE slug = ?
            """,
            (note["slug"],),
        ).fetchone()

        if row is None:
            connection.execute(
                """
                INSERT INTO author_notes (
                    slug, title, url, intro, published_at,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (
                    note["slug"],
                    note["title"],
                    note["url"],
                    note["intro"],
                    note["published_at"],
                    now,
                    now,
                ),
            )
            return "created"

        # A sent note is the record of what subscribers received.
        if row["status"] == "sent":
            return "sent"

        # Any edit drops the approval: only the exact reviewed text may be sent.
        connection.execute(
            """
            UPDATE author_notes
            SET title = ?,
                url = ?,
                intro = ?,
                published_at = ?,
                status = 'draft',
                approved_at = NULL,
                updated_at = ?
            WHERE slug = ?
              AND status != 'sent'
            """,
            (
                note["title"],
                note["url"],
                note["intro"],
                note["published_at"],
                now,
                note["slug"],
            ),
        )

    return "updated"


def set_note_approval(slug: str, approved: bool) -> str:
    """Returns ok, missing or sent."""
    now = _utc_timestamp()

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT status
            FROM author_notes
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()

        if row is None:
            return "missing"

        if row["status"] == "sent":
            return "sent"

        target = "approved" if approved else "draft"
        if row["status"] == target:
            return "ok"

        connection.execute(
            """
            UPDATE author_notes
            SET status = ?,
                approved_at = ?,
                updated_at = ?
            WHERE slug = ?
              AND status != 'sent'
            """,
            (target, now if approved else None, now, slug),
        )

    return "ok"


def get_pending_note(edition_date: str) -> dict | None:
    """Oldest approved note, unless this edition already carried one."""
    with get_connection() as connection:
        taken = connection.execute(
            """
            SELECT 1
            FROM author_notes
            WHERE edition_date = ?
            """,
            (edition_date,),
        ).fetchone()

        if taken is not None:
            return None

        row = connection.execute(
            f"""
            SELECT {_NOTE_COLUMNS}
            FROM author_notes
            WHERE status = 'approved'
            ORDER BY approved_at, id
            LIMIT 1
            """
        ).fetchone()

    return dict(row) if row else None


def mark_note_sent(note_id: int, edition_date: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE author_notes
            SET status = 'sent',
                edition_date = ?,
                sent_at = ?,
                updated_at = ?
            WHERE id = ?
              AND status = 'approved'
            """,
            (edition_date, _utc_timestamp(), _utc_timestamp(), note_id),
        )

    return cursor.rowcount == 1


def get_edition_note(edition_date: str) -> dict | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT title, url, intro
            FROM author_notes
            WHERE status = 'sent'
              AND edition_date = ?
            ORDER BY id
            LIMIT 1
            """,
            (edition_date,),
        ).fetchone()

    if row is None:
        return None

    # The column is NOT NULL, so a note without a link stores an empty string.
    return {**dict(row), "url": row["url"] or None}


def backup_database(output: Path) -> None:
    """Write a consistent copy of the live database, safe while the app is running."""
    output.parent.mkdir(parents=True, exist_ok=True)

    with get_connection() as source:
        target = sqlite3.connect(output)
        try:
            source.backup(target)
        finally:
            target.close()


def database_summary(path: Path | None = None) -> dict[str, str | int | None]:
    """Integrity and row counts, to compare a copy against its source."""
    counts = {
        "articles": "SELECT COUNT(*) FROM articles",
        "delivered_articles": "SELECT COUNT(*) FROM articles WHERE delivered_at IS NOT NULL",
        "active_subscribers": "SELECT COUNT(*) FROM subscribers WHERE status = 'active'",
        "subscribers": "SELECT COUNT(*) FROM subscribers",
        "notes": "SELECT COUNT(*) FROM author_notes",
        "last_edition": "SELECT MAX(date(delivered_at)) FROM articles",
        "last_job": "SELECT MAX(finished_at) FROM job_runs",
    }
    # Read-only, so checking a copy never creates or changes a file.
    # as_uri() keeps odd paths (relative, doubled slashes) from being read as a URI authority.
    uri = f"{Path(path or DB_PATH).resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)

    try:
        summary: dict[str, str | int | None] = {
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0]
        }
        for name, query in counts.items():
            try:
                summary[name] = connection.execute(query).fetchone()[0]
            except sqlite3.OperationalError:
                # A database from before the table existed.
                summary[name] = None
    finally:
        connection.close()

    return summary
