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
