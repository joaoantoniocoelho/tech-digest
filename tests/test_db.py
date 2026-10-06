import runpy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db


class MarkArticlesDeliveredTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temporary.close()
        self.db_path = Path(temporary.name)
        self.db_patcher = patch.object(db, "DB_PATH", self.db_path)
        self.db_patcher.start()
        db.init_db()

    def tearDown(self):
        self.db_patcher.stop()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)

    def _insert(self, title, delivered_at=None):
        with db.get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO articles (
                    source, title, url, relevance_score, processed_at,
                    delivered_at
                )
                VALUES ('Feed', ?, ?, 80, '2026-09-22 09:00:00', ?)
                """,
                (title, f"https://example.com/{title}", delivered_at),
            )
            return cursor.lastrowid

    def _delivered_at(self, article_id):
        with db.get_connection() as connection:
            return connection.execute(
                "SELECT delivered_at FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()["delivered_at"]

    def test_stores_send_time_as_utc_edition_date(self):
        article_id = self._insert("selected")

        db.mark_articles_delivered(
            article_ids=[article_id],
            delivered_at=datetime(
                2026, 9, 22, 22, 30, 15,
                tzinfo=timezone(timedelta(hours=-3)),
            ),
        )

        self.assertEqual(self._delivered_at(article_id), "2026-09-23 01:30:15")
        self.assertIsNotNone(db.get_public_edition("2026-09-23"))
        self.assertIsNone(db.get_public_edition("2026-09-22"))

    def test_default_matches_current_timestamp_format(self):
        article_id = self._insert("selected")

        db.mark_articles_delivered(article_ids=[article_id])

        with db.get_connection() as connection:
            now = connection.execute("SELECT CURRENT_TIMESTAMP AS now").fetchone()["now"]
        sqlite_format = "%Y-%m-%d %H:%M:%S"
        stored = datetime.strptime(self._delivered_at(article_id), sqlite_format)
        current = datetime.strptime(now, sqlite_format)
        self.assertLessEqual(abs(current - stored), timedelta(seconds=5))

    def test_keeps_existing_delivery_time(self):
        article_id = self._insert("old", delivered_at="2026-09-20 10:00:00")

        db.mark_articles_delivered(
            article_ids=[article_id],
            delivered_at=datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(self._delivered_at(article_id), "2026-09-20 10:00:00")

    def test_latest_edition_orders_legacy_and_new_rows(self):
        # Rows written by CURRENT_TIMESTAMP before this change must still sort
        # correctly against rows stamped with the send time.
        self._insert("legacy", delivered_at="2026-09-21 10:00:07")
        article_id = self._insert("new")

        db.mark_articles_delivered(
            article_ids=[article_id],
            delivered_at=datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc),
        )

        edition = db.get_latest_edition()
        self.assertEqual(
            [article["title"] for article in edition["articles"]],
            ["new"],
        )


if __name__ == "__main__":
    unittest.main()
