import runpy
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import yaml

from app import db, rescore

CONFIG = {
    "minimum_score": 60,
    "maximum_articles": 5,
    "source_limits": {"Hacker News": 1},
    "title_filters": {"blocked_phrases": ["is hiring"], "max_launch_posts": 1},
}


def _profile():
    with open("config/interests.yaml") as file:
        return yaml.safe_load(file)


def _stored(title, importance, source="Example", delivered=False, **strengths):
    return {
        "source": source,
        "title": title,
        "relevance_score": 0,
        "importance": importance,
        "feature_strengths": strengths,
        "day": "2026-11-25",
        "delivered": delivered,
    }


class RescoreTestCase(unittest.TestCase):
    def test_candidate_formula_separates_shallow_from_notable(self):
        articles = [
            _stored("Shallow", 0, security=2, privacy=2),
            _stored("Notable", 2, security=2, privacy=2),
            _stored("Retired feature", 1, security=2, no_longer_in_profile=2),
        ]

        current = rescore.rescore(articles, _profile(), {0: -5, 1: 0, 2: 5, 3: 8})
        candidate = rescore.rescore(articles, _profile(), {0: -30, 1: -8, 2: 5, 3: 12})

        self.assertEqual([article["new_score"] for article in current], [61, 71, 55])
        self.assertEqual([article["new_score"] for article in candidate], [36, 71, 47])

    def test_edition_applies_cutoff_title_rules_and_source_caps(self):
        articles = [
            {**_stored("HN top", 1, source="Hacker News"), "new_score": 90},
            {**_stored("HN second", 1, source="Hacker News"), "new_score": 85},
            {**_stored("Acme is hiring", 1), "new_score": 80},
            {**_stored("Show HN: one", 1), "new_score": 75},
            {**_stored("Show HN: two", 1), "new_score": 74},
            {**_stored("Blog post", 1), "new_score": 70},
            {**_stored("Below the cutoff", 1), "new_score": 59},
        ]

        edition = rescore.select_edition(articles, 60, 5, CONFIG)

        self.assertEqual(
            [article["title"] for article in edition],
            ["HN top", "Show HN: one", "Blog post"],
        )

    def test_report_marks_what_was_sent_and_what_would_drop(self):
        articles = [
            {**_stored("Kept", 2, delivered=True), "new_score": 80},
            {**_stored("Newly picked", 2), "new_score": 75},
            {**_stored("Would drop", 0, delivered=True), "new_score": 40},
        ]

        report = rescore.render_report(articles, 60, 5, CONFIG)

        self.assertIn("2026-11-25: 3 classified, 2 at or above 60, 2 selected", report)
        self.assertIn("[sent]  80", report)
        self.assertIn("[new ]  75", report)
        self.assertIn("[drop]  40", report)

    def test_report_without_stored_answers(self):
        self.assertIn("No stored classifier answers", rescore.render_report([], 60, 5, CONFIG))

    def test_importance_argument_needs_four_numbers(self):
        self.assertEqual(rescore.parse_adjustments("-30,-8,5,12"), {0: -30, 1: -8, 2: 5, 3: 12})

        with self.assertRaisesRegex(ValueError, "four"):
            rescore.parse_adjustments("1,2")


class StoredAnswersTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(self.tmp.name) / "digest.db")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _save(self, title, result):
        url = f"https://example.com/{title}"
        db.save_article(
            {
                "source": "Example",
                "title": title,
                "url": url,
                "published_at": "2026-11-25T10:00:00+00:00",
                "feed_excerpt": "",
            }
        )
        db.save_classification(db.get_article_by_url(url)["id"], result)

    def test_only_articles_with_stored_answers_are_returned(self):
        db.init_db()
        base = {"relevance_score": 70, "why_interesting": "", "topics": []}
        self._save("with-answers", {**base, "importance": 2, "feature_strengths": {"security": 2}})
        self._save("classified-before-the-columns", base)

        stored = db.get_scored_articles(7)

        self.assertEqual([article["title"] for article in stored], ["with-answers"])
        self.assertEqual(stored[0]["importance"], 2)
        self.assertEqual(stored[0]["feature_strengths"], {"security": 2})
        self.assertFalse(stored[0]["delivered"])

    def test_columns_are_added_to_an_existing_database(self):
        with sqlite3.connect(db.DB_PATH) as connection:
            connection.execute(
                """
                CREATE TABLE articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    published_at TEXT,
                    discovered_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                "INSERT INTO articles (source, title, url) VALUES ('Old', 'Old row', 'https://o')"
            )

        db.init_db()

        with db.get_connection() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(articles)")}
            row = connection.execute("SELECT title, importance FROM articles").fetchone()

        self.assertLessEqual({"importance", "feature_strengths"}, columns)
        self.assertEqual((row["title"], row["importance"]), ("Old row", None))

    def test_cli_runs_against_an_empty_database(self):
        with redirect_stdout(StringIO()) as output:
            rescore.main(["--days", "3", "--importance=-30,-8,5,12"])

        self.assertIn("No stored classifier answers", output.getvalue())


if __name__ == "__main__":
    unittest.main()
