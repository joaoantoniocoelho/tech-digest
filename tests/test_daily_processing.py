import ast
import runpy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import yaml

from app import db, processor
from app.db import (
    get_articles_for_daily_processing,
    get_digest_candidates,
    init_db,
    record_processing_error,
    save_classification,
)
from app.processor import (
    MAX_PROCESSING_ATTEMPTS,
    _prepare_classification_content,
    process_articles,
)


def _hours_ago(hours: float) -> str:
    return (
        datetime.now(timezone.utc)
        - timedelta(hours=hours)
    ).isoformat()


class DailyProcessingTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(
            suffix=".db",
            delete=False,
        )
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_patcher = patch.object(
            db,
            "DB_PATH",
            self.db_path,
        )
        self.db_patcher.start()
        init_db()
        self._url_counter = 0

    def tearDown(self):
        self.db_patcher.stop()
        self.db_path.unlink(missing_ok=True)

    def _insert(
        self,
        *,
        source="Test Source",
        title="Test Title",
        published_at=None,
        discovered_at=None,
        processed_at=None,
        failed_at=None,
        processing_attempts=0,
        feed_excerpt=None,
        relevance_score=None,
        delivered_at=None,
    ):
        self._url_counter += 1
        url = (
            "https://example.com/article-"
            f"{self._url_counter}"
        )

        if discovered_at is None:
            discovered_at = (
                datetime.now(timezone.utc)
                .isoformat()
            )

        with db.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO articles (
                    source,
                    title,
                    url,
                    published_at,
                    discovered_at,
                    processed_at,
                    failed_at,
                    processing_attempts,
                    feed_excerpt,
                    relevance_score,
                    delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    title,
                    url,
                    published_at,
                    discovered_at,
                    processed_at,
                    failed_at,
                    processing_attempts,
                    feed_excerpt,
                    relevance_score,
                    delivered_at,
                ),
            )

            row = connection.execute(
                "SELECT id FROM articles WHERE url = ?",
                (url,),
            ).fetchone()

        return {
            "id": row["id"],
            "source": source,
            "title": title,
            "url": url,
        }

    def test_recent_article_is_eligible(self):
        article = self._insert(
            published_at=_hours_ago(2),
        )

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )

        self.assertEqual(
            [item["id"] for item in eligible],
            [article["id"]],
        )

    def test_old_article_is_not_eligible(self):
        self._insert(
            title="Old imported article",
            published_at=_hours_ago(48),
            discovered_at=_hours_ago(1),
        )

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )

        self.assertEqual(eligible, [])

    def test_uses_discovered_at_when_published_at_missing(self):
        recent = self._insert(
            title="No publication date",
            published_at=None,
            discovered_at=_hours_ago(3),
        )
        self._insert(
            title="Old discovery",
            published_at=None,
            discovered_at=_hours_ago(48),
        )

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )

        self.assertEqual(
            [item["id"] for item in eligible],
            [recent["id"]],
        )

    def test_prefers_published_at_over_recent_discovery(self):
        self._insert(
            title="Historical RSS entry",
            published_at=_hours_ago(72),
            discovered_at=_hours_ago(1),
        )

        eligible = get_articles_for_daily_processing(
            lookback_hours=28,
        )

        self.assertEqual(eligible, [])

    def test_processes_all_eligible_articles_without_batch_limit(self):
        inserted = [
            self._insert(
                source=f"Source {index % 5}",
                title=f"Article {index}",
                published_at=_hours_ago(1),
            )
            for index in range(25)
        ]

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )

        self.assertEqual(len(eligible), 25)
        self.assertEqual(
            {item["id"] for item in eligible},
            {item["id"] for item in inserted},
        )

    def test_in_flight_retry_remains_eligible_outside_window(self):
        retry = self._insert(
            title="Retry me",
            published_at=_hours_ago(48),
            processing_attempts=1,
        )
        self._insert(
            title="Never attempted historical",
            published_at=_hours_ago(48),
            processing_attempts=0,
        )

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )

        self.assertEqual(
            [item["id"] for item in eligible],
            [retry["id"]],
        )

    def test_failed_and_processed_articles_are_excluded(self):
        self._insert(
            title="Already classified",
            published_at=_hours_ago(2),
            processed_at=_hours_ago(1),
            relevance_score=80,
        )
        self._insert(
            title="Permanently failed",
            published_at=_hours_ago(2),
            failed_at=_hours_ago(1),
            processing_attempts=3,
        )

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )

        self.assertEqual(eligible, [])

    def test_digest_still_uses_publication_window(self):
        recent = self._insert(
            title="Recent classified",
            published_at=_hours_ago(3),
            processed_at=_hours_ago(1),
            relevance_score=80,
        )
        self._insert(
            title="Old classified",
            published_at=_hours_ago(48),
            discovered_at=_hours_ago(1),
            processed_at=_hours_ago(1),
            relevance_score=90,
        )
        self._insert(
            title="Already delivered",
            published_at=_hours_ago(2),
            processed_at=_hours_ago(1),
            relevance_score=95,
            delivered_at=_hours_ago(1),
        )

        candidates = get_digest_candidates(
            lookback_hours=24,
            minimum_score=60,
            maximum_articles=8,
        )

        self.assertEqual(
            [item["id"] for item in candidates],
            [recent["id"]],
        )


class ProcessorSafeguardTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(
            suffix=".db",
            delete=False,
        )
        tmp.close()
        self.db_path = Path(tmp.name)
        self.db_patcher = patch.object(
            db,
            "DB_PATH",
            self.db_path,
        )
        self.db_patcher.start()
        init_db()

        with db.get_connection() as connection:
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
                    "Test Source",
                    "Test Title",
                    "https://example.com/article",
                    _hours_ago(2),
                ),
            )
            self.article_id = connection.execute(
                "SELECT id FROM articles WHERE url = ?",
                ("https://example.com/article",),
            ).fetchone()["id"]

        self.article = {
            "id": self.article_id,
            "source": "Test Source",
            "title": "Test Title",
            "url": "https://example.com/article",
            "feed_excerpt": None,
        }

    def tearDown(self):
        self.db_patcher.stop()
        self.db_path.unlink(missing_ok=True)

    def _load_zero_features(self):
        with open("config/interests.yaml") as file:
            profile = yaml.safe_load(file)

        return {
            feature_id: 0
            for feature_id in profile["features"]
        }

    def _row(self):
        with db.get_connection() as connection:
            return connection.execute(
                "SELECT * FROM articles WHERE id = ?",
                (self.article_id,),
            ).fetchone()

    def test_truncates_long_content_head_and_tail(self):
        content = "a" * 30000
        prepared = _prepare_classification_content(
            content
        )

        self.assertLess(len(prepared), len(content))
        self.assertTrue(prepared.startswith("a" * 20000))
        self.assertTrue(prepared.endswith("a" * 5000))
        self.assertIn(
            "[... article content omitted ...]",
            prepared,
        )

    @patch("app.processor.classify_article")
    @patch("app.processor.fetch_article_content")
    def test_successful_classification_is_not_reselected(
        self,
        fetch_article_content,
        classify_article,
    ):
        fetch_article_content.return_value = "x" * 300
        classify_article.return_value = {
            "feature_strengths": self._load_zero_features(),
            "importance": 1,
            "why_interesting": "Useful background.",
            "topics": ["Testing"],
        }

        summary = process_articles([self.article])

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertIsNotNone(self._row()["processed_at"])

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )
        self.assertEqual(eligible, [])
        classify_article.assert_called_once()

    @patch("app.processor.classify_article")
    @patch("app.processor.fetch_article_content")
    def test_old_article_does_not_call_classifier(
        self,
        fetch_article_content,
        classify_article,
    ):
        with db.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO articles (
                    source,
                    title,
                    url,
                    published_at,
                    discovered_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "Test Source",
                    "Historical",
                    "https://example.com/old",
                    _hours_ago(72),
                    _hours_ago(1),
                ),
            )

            historical_id = connection.execute(
                "SELECT id FROM articles WHERE url = ?",
                ("https://example.com/old",),
            ).fetchone()["id"]

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )
        historical = [
            article
            for article in eligible
            if article["id"] == historical_id
        ]

        self.assertEqual(historical, [])

        summary = process_articles(historical)

        self.assertEqual(summary["processed"], 0)
        classify_article.assert_not_called()
        fetch_article_content.assert_not_called()

    @patch("app.processor.fetch_article_content")
    def test_extraction_failure_increments_attempts(
        self,
        fetch_article_content,
    ):
        fetch_article_content.return_value = None

        summary = process_articles([self.article])

        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["failed"], 1)

        row = self._row()
        self.assertEqual(row["processing_attempts"], 1)
        self.assertIsNone(row["failed_at"])
        self.assertIsNone(row["processed_at"])

    @patch("app.processor.fetch_article_content")
    def test_marks_failed_after_max_attempts(
        self,
        fetch_article_content,
    ):
        fetch_article_content.return_value = None

        for _ in range(MAX_PROCESSING_ATTEMPTS):
            process_articles([self.article])

        row = self._row()
        self.assertEqual(
            row["processing_attempts"],
            MAX_PROCESSING_ATTEMPTS,
        )
        self.assertIsNotNone(row["failed_at"])

        eligible = get_articles_for_daily_processing(
            lookback_hours=24,
        )
        self.assertEqual(eligible, [])

    def test_save_classification_clears_previous_error(self):
        record_processing_error(
            article_id=self.article_id,
            error="temporary failure",
            fail_after_attempts=MAX_PROCESSING_ATTEMPTS,
        )

        save_classification(
            article_id=self.article_id,
            result={
                "relevance_score": 70,
                "why_interesting": "Cleared.",
                "topics": ["Retry"],
            },
        )

        row = self._row()
        self.assertIsNotNone(row["processed_at"])
        self.assertIsNone(row["failed_at"])
        self.assertIsNone(row["last_processing_error"])

    @patch("app.processor.classify_article")
    @patch("app.processor.fetch_article_content")
    def test_classifier_exception_increments_attempts(
        self,
        fetch_article_content,
        classify_article,
    ):
        fetch_article_content.return_value = "x" * 300
        classify_article.side_effect = RuntimeError(
            "TypeSafe down"
        )

        summary = process_articles([self.article])

        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["failed"], 1)

        row = self._row()
        self.assertEqual(row["processing_attempts"], 1)
        self.assertIsNone(row["failed_at"])
        self.assertIsNone(row["processed_at"])
        self.assertIn(
            "TypeSafe down",
            row["last_processing_error"],
        )

    @patch("app.processor.classify_article")
    @patch("app.processor.fetch_article_content")
    def test_save_classification_does_not_persist_content(
        self,
        fetch_article_content,
        classify_article,
    ):
        fetch_article_content.return_value = (
            "secret article body " * 20
        )
        classify_article.return_value = {
            "feature_strengths": self._load_zero_features(),
            "importance": 1,
            "why_interesting": "AI agents",
            "topics": ["AI agents"],
        }

        process_articles([self.article])

        row = self._row()
        values = [
            str(row[key])
            for key in row.keys()
            if row[key] is not None
        ]

        self.assertTrue(
            all(
                "secret article body" not in value
                for value in values
            )
        )

        with db.get_connection() as connection:
            columns = {
                info["name"]
                for info in connection.execute(
                    "PRAGMA table_info(articles)"
                )
            }

        self.assertNotIn("content", columns)
        self.assertNotIn("probabilities", columns)
        self.assertNotIn("confidence", columns)
        self.assertNotIn("raw_jev", columns)


class CollectionEntryPointTestCase(unittest.TestCase):
    def test_main_does_not_import_processor(self):
        source = Path("app/main.py").read_text()
        tree = ast.parse(source)
        imported_names = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.name)

        self.assertNotIn("process_articles", imported_names)
        self.assertNotIn("process_daily", imported_names)
        self.assertNotIn("classify_article", imported_names)

    def test_processor_has_no_batch_size(self):
        source = Path("app/processor.py").read_text()
        self.assertNotIn("PROCESSING_BATCH_SIZE", source)
        self.assertNotIn("Batch distribution", source)

    def test_db_has_no_round_robin_selection(self):
        source = Path("app/db.py").read_text()
        self.assertNotIn("ROW_NUMBER()", source)
        self.assertNotIn("PARTITION BY source", source)


if __name__ == "__main__":
    unittest.main()
