import json
import sqlite3
from pathlib import Path


DB_PATH = Path("data/digest.db")


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    return connection


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
                discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(articles)")
        }

        if "relevance_score" not in columns:
            connection.execute(
                """
                ALTER TABLE articles
                ADD COLUMN relevance_score INTEGER
                """
            )

        if "why_interesting" not in columns:
            connection.execute(
                """
                ALTER TABLE articles
                ADD COLUMN why_interesting TEXT
                """
            )

        if "topics" not in columns:
            connection.execute(
                """
                ALTER TABLE articles
                ADD COLUMN topics TEXT
                """
            )

        if "processed_at" not in columns:
            connection.execute(
                """
                ALTER TABLE articles
                ADD COLUMN processed_at TEXT
                """
            )


def save_article(article: dict) -> bool:
    try:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO articles (
                    source,
                    title,
                    url,
                    published_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    article["source"],
                    article["title"],
                    article["url"],
                    article["published_at"],
                ),
            )

        return True

    except sqlite3.IntegrityError:
        return False


def get_unprocessed_articles(limit: int = 10):
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                source,
                title,
                url
            FROM articles
            WHERE processed_at IS NULL
            ORDER BY id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return rows


def save_classification(article_id: int, result: dict):
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE articles
            SET relevance_score = ?,
                why_interesting = ?,
                topics = ?,
                processed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                result["relevance_score"],
                result["why_interesting"],
                json.dumps(result["topics"]),
                article_id,
            ),
        )
