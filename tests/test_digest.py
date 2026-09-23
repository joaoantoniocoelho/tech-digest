import os
import runpy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.classifier import is_duplicate_story
from app.digest import build_digest


def _article(title, score=80, excerpt=""):
    return {
        "title": title,
        "source": "Example",
        "feed_excerpt": excerpt,
        "relevance_score": score,
    }


class DuplicateStoryTestCase(unittest.TestCase):
    @patch("app.classifier._get_client")
    def test_jev_compares_candidate_with_selected_stories(self, get_client):
        client = get_client.return_value
        client.system_one.return_value = SimpleNamespace(
            scores={
                "duplicate": SimpleNamespace(score=2.0),
            }
        )
        selected = [_article("Introducing GPT-6 Sol and Luna")]
        candidate = _article(
            "OpenAI launches GPT-6 Sol and Luna",
            excerpt="The new models have lower prices.",
        )

        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            self.assertTrue(is_duplicate_story(candidate, selected))

        call = client.system_one.call_args.kwargs
        self.assertEqual(call["state"]["candidate"]["title"], candidate["title"])
        self.assertEqual(
            call["state"]["selected_stories"][0]["title"],
            selected[0]["title"],
        )
        self.assertEqual(call["state"]["candidate"]["excerpt"], candidate["feed_excerpt"])
        self.assertEqual(call["model"], "jev-1.13.0")

    @patch("app.classifier._get_client")
    def test_related_topic_does_not_count_as_duplicate(self, get_client):
        get_client.return_value.system_one.return_value = SimpleNamespace(
            scores={"duplicate": SimpleNamespace(score=1.0)}
        )

        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            self.assertFalse(
                is_duplicate_story(
                    _article("Opus 5.5 performance analysis"),
                    [_article("Claude Opus 5.5 announcement")],
                )
            )

    @patch("app.classifier._get_client")
    def test_invalid_jev_response_stops_selection(self, get_client):
        get_client.return_value.system_one.return_value = SimpleNamespace(
            scores={}
        )

        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            with self.assertRaisesRegex(ValueError, "duplicate score"):
                is_duplicate_story(
                    _article("Another story"),
                    [_article("A story")],
                )


class DigestSelectionTestCase(unittest.TestCase):
    @patch("app.digest.is_duplicate_story")
    @patch("app.digest.get_digest_candidates")
    @patch("app.digest.load_digest_config")
    @patch("app.digest.init_db")
    def test_skips_lower_scored_duplicate_and_fills_slot(
        self, init_db, load_config, get_candidates, is_duplicate
    ):
        load_config.return_value = {
            "lookback_hours": 24,
            "minimum_score": 60,
            "maximum_articles": 3,
        }
        get_candidates.return_value = [
            _article("Introducing GPT-6 Sol and Luna", 95),
            _article("OpenAI launches GPT-6 Sol and Luna", 90),
            _article("AstroForge puts AI on spacecraft", 85),
            _article("A new compiler release", 80),
            _article("An extra article", 70),
        ]
        is_duplicate.side_effect = [True, False, False]

        digest = build_digest()

        self.assertEqual(
            [article["title"] for article in digest["articles"]],
            [
                "Introducing GPT-6 Sol and Luna",
                "AstroForge puts AI on spacecraft",
                "A new compiler release",
            ],
        )
        self.assertEqual(is_duplicate.call_count, 3)
        get_candidates.assert_called_once_with(
            lookback_hours=24,
            minimum_score=60,
        )

    @patch("app.digest.is_duplicate_story")
    @patch("app.digest.get_digest_candidates")
    @patch("app.digest.load_digest_config")
    @patch("app.digest.init_db")
    def test_identical_titles_are_skipped_without_jev(
        self, init_db, load_config, get_candidates, is_duplicate
    ):
        load_config.return_value = {
            "lookback_hours": 24,
            "minimum_score": 60,
            "maximum_articles": 2,
        }
        get_candidates.return_value = [
            _article("Claude Opus 5.5", 90),
            _article("  claude  opus 5.5 ", 85),
            _article("A distinct article", 80),
        ]
        is_duplicate.return_value = False

        digest = build_digest()

        self.assertEqual(len(digest["articles"]), 2)
        self.assertEqual(is_duplicate.call_count, 1)


class CandidateQueryTestCase(unittest.TestCase):
    def test_can_fetch_beyond_digest_limit_for_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(db, "DB_PATH", Path(tmp) / "digest.db"):
                db.init_db()

                for score in (90, 80, 70):
                    url = f"https://example.com/{score}"
                    db.save_article({
                        "source": "Example",
                        "title": f"Story {score}",
                        "url": url,
                        "published_at": datetime.now(timezone.utc).isoformat(),
                        "feed_excerpt": f"Excerpt {score}",
                    })
                    db.save_classification(
                        db.get_article_by_url(url)["id"],
                        {
                            "relevance_score": score,
                            "why_interesting": "",
                            "topics": [],
                        },
                    )

                all_candidates = db.get_digest_candidates(24, 60)
                limited = db.get_digest_candidates(24, 60, 2)

        self.assertEqual(
            [item["relevance_score"] for item in all_candidates],
            [90, 80, 70],
        )
        self.assertEqual(len(limited), 2)
        self.assertEqual(all_candidates[0]["feed_excerpt"], "Excerpt 90")


if __name__ == "__main__":
    unittest.main()
