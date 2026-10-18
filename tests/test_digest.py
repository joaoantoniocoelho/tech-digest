import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.classifier import is_duplicate_story
from app.digest import build_digest, main


def _article(title, score=80, excerpt="", topics=None):
    return {
        "title": title,
        "source": "Example",
        "feed_excerpt": excerpt,
        "relevance_score": score,
        "topics": topics or [],
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
        get_client.return_value.system_one.return_value = SimpleNamespace(scores={})

        with (
            patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}),
            self.assertRaisesRegex(ValueError, "duplicate score"),
        ):
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


class DiversitySelectionTestCase(unittest.TestCase):
    CONFIG = {
        "lookback_hours": 24,
        "minimum_score": 60,
        "maximum_articles": 4,
        "diversity": {
            "penalty": 8,
            "free_per_group": {"ai": 2},
        },
    }
    GROUPS = {
        "AI models": "ai",
        "AI agents": "ai",
        "Security": "security",
        "Software engineering": "engineering",
    }

    def _build(self, candidates, config=None):
        with (
            patch("app.digest.init_db"),
            patch(
                "app.digest.load_digest_config",
                return_value=config or self.CONFIG,
            ),
            patch(
                "app.digest.get_digest_candidates",
                return_value=candidates,
            ),
            patch(
                "app.digest.load_topic_groups",
                return_value=self.GROUPS,
            ),
            patch(
                "app.digest.is_duplicate_story",
                return_value=False,
            ),
        ):
            return [article["title"] for article in build_digest()["articles"]]

    def test_extra_items_from_a_full_group_rank_lower(self):
        titles = self._build(
            [
                _article("AI 1", 90, topics=["AI models"]),
                _article("AI 2", 88, topics=["AI agents"]),
                _article("AI 3", 86, topics=["AI models", "Security"]),
                _article("AI 4", 84, topics=["AI agents"]),
                _article("Security", 75, topics=["Security"]),
                _article("Engineering", 70, topics=["Software engineering"]),
            ]
        )

        self.assertEqual(
            titles,
            ["AI 1", "AI 2", "AI 3", "Security"],
        )

    def test_strong_items_still_win_over_much_weaker_ones(self):
        titles = self._build(
            [
                _article("AI 1", 95, topics=["AI models"]),
                _article("AI 2", 94, topics=["AI models"]),
                _article("AI 3", 93, topics=["AI models"]),
                _article("AI 4", 92, topics=["AI models"]),
                _article("Security", 61, topics=["Security"]),
            ]
        )

        self.assertEqual(titles, ["AI 1", "AI 2", "AI 3", "AI 4"])

    def test_diversity_never_adds_articles_below_the_candidates(self):
        titles = self._build(
            [
                _article("AI 1", 90, topics=["AI models"]),
                _article("AI 2", 88, topics=["AI models"]),
                _article("AI 3", 86, topics=["AI models"]),
            ]
        )

        self.assertEqual(titles, ["AI 1", "AI 2", "AI 3"])

    def test_without_diversity_config_order_is_by_score(self):
        config = dict(self.CONFIG)
        del config["diversity"]

        titles = self._build(
            [
                _article("AI 1", 90, topics=["AI models"]),
                _article("AI 2", 88, topics=["AI models"]),
                _article("AI 3", 86, topics=["AI models"]),
                _article("Security", 70, topics=["Security"]),
            ],
            config=config,
        )

        self.assertEqual(titles, ["AI 1", "AI 2", "AI 3", "Security"])

    def test_every_positive_feature_has_a_known_group(self):
        import yaml

        from app.digest import INTERESTS_PATH

        with INTERESTS_PATH.open() as file:
            features = yaml.safe_load(file)["features"]

        for feature_id, feature in features.items():
            if feature["weight"] > 0:
                self.assertIn(
                    feature.get("group"),
                    {"ai", "engineering", "security", "business", "systems"},
                    feature_id,
                )


class CandidateQueryTestCase(unittest.TestCase):
    def test_can_fetch_beyond_digest_limit_for_replacements(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(db, "DB_PATH", Path(tmp) / "digest.db"),
        ):
            db.init_db()

            for score in (90, 80, 70):
                url = f"https://example.com/{score}"
                db.save_article(
                    {
                        "source": "Example",
                        "title": f"Story {score}",
                        "url": url,
                        "published_at": datetime.now(timezone.utc).isoformat(),
                        "feed_excerpt": f"Excerpt {score}",
                    }
                )
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


class HtmlPreviewTestCase(unittest.TestCase):
    def test_sample_preview_writes_html_without_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "digest.html"

            with redirect_stdout(StringIO()):
                main(["--sample", "--html", str(path)])

            html = path.read_text(encoding="utf-8")

        self.assertIn("TECH DIGEST", html)
        self.assertIn("Formal methods with Hillel Wayne", html)
        self.assertIn("The last six months in LLMs, in five minutes", html)
        self.assertIn("What changed in SQLite this year", html)
        self.assertIn(
            "https://digest.joaoac.com/unsubscribe/preview",
            html,
        )
        self.assertNotIn("Read article", html)
        self.assertIn("Technology worth your time.", html)


if __name__ == "__main__":
    unittest.main()
