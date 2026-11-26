import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.classifier import is_duplicate_story
from app.digest import build_digest, main


def _article(title, score=80, excerpt="", topics=None, source="Example"):
    return {
        "title": title,
        "source": source,
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


class EditorialRulesTestCase(unittest.TestCase):
    def _build(self, candidates, config, recent=None, is_duplicate=None):
        base = {"lookback_hours": 24, "minimum_score": 60, "maximum_articles": 8}
        self.is_duplicate = is_duplicate or (lambda candidate, known: False)
        self.duplicate_calls = []

        def record(candidate, known):
            self.duplicate_calls.append((candidate["title"], [story["title"] for story in known]))
            return self.is_duplicate(candidate, known)

        with (
            patch("app.digest.init_db"),
            patch("app.digest.load_digest_config", return_value={**base, **config}),
            patch("app.digest.get_digest_candidates", return_value=candidates),
            patch("app.digest.get_recently_delivered", return_value=recent or []) as get_recent,
            patch("app.digest.is_duplicate_story", side_effect=record),
        ):
            self.get_recent = get_recent
            return [article["title"] for article in build_digest()["articles"]]

    def test_source_limit_holds_even_when_the_source_has_the_top_scores(self):
        candidates = [
            _article(f"HN {score}", score, source="Hacker News") for score in range(95, 87, -1)
        ] + [_article("Blog post", 61, source="Julia Evans")]

        titles = self._build(candidates, {"source_limits": {"Hacker News": 3}})

        self.assertEqual(titles, ["HN 95", "HN 94", "HN 93", "Blog post"])

    def test_sources_without_a_limit_are_not_capped(self):
        candidates = [_article(f"Post {score}", score) for score in (90, 85, 80, 75)]

        titles = self._build(candidates, {"source_limits": {"Hacker News": 3}})

        self.assertEqual(len(titles), 4)

    def test_only_one_launch_post_per_edition(self):
        titles = self._build(
            [
                _article("Show HN: A tiny database", 90),
                _article("Launch HN: Tasklet (YC P26)", 85),
                _article("show hn: another tool", 80),
                _article("How we show HN traffic", 75),
            ],
            {"title_filters": {"max_launch_posts": 1}},
        )

        self.assertEqual(titles, ["Show HN: A tiny database", "How we show HN traffic"])

    def test_blocked_titles_never_enter(self):
        titles = self._build(
            [
                _article("Tasklet (YC P26) Is Hiring a founding engineer", 95),
                _article("Fuck Your AI Safeguards", 90),
                _article("Switcher (2004)", 85),
                _article("A good article", 70),
            ],
            {
                "title_filters": {
                    "blocked_phrases": ["is hiring"],
                    "blocked_words": ["fuck"],
                    "block_dated_titles": True,
                }
            },
        )

        self.assertEqual(titles, ["A good article"])

    def test_story_delivered_in_the_window_is_skipped(self):
        recent = [{"title": "Inkling 2 Mini", "feed_excerpt": ""}]

        titles = self._build(
            [_article("Inkling 2 Mini is here", 90), _article("Another story", 80)],
            {"dedup_lookback_days": 3},
            recent=recent,
            is_duplicate=lambda candidate, known: candidate["title"].startswith("Inkling"),
        )

        self.assertEqual(titles, ["Another story"])
        self.get_recent.assert_called_once_with(3)
        self.assertEqual(
            self.duplicate_calls,
            [
                ("Inkling 2 Mini is here", ["Inkling 2 Mini"]),
                ("Another story", ["Inkling 2 Mini"]),
            ],
        )

    def test_without_lookback_only_the_edition_is_compared(self):
        titles = self._build([_article("One", 90), _article("Two", 80)], {})

        self.assertEqual(titles, ["One", "Two"])
        self.get_recent.assert_not_called()
        self.assertEqual(self.duplicate_calls, [("Two", ["One"])])


class DailyFloorTestCase(unittest.TestCase):
    CONFIG = {
        "lookback_hours": 24,
        "minimum_score": 70,
        "maximum_articles": 5,
        "minimum_articles": 3,
        "fallback_minimum_score": 60,
    }

    def _build(self, scores, config=None, is_duplicate=None):
        candidates = [_article(f"Story {score}", score) for score in scores]

        with (
            patch("app.digest.init_db"),
            patch("app.digest.load_digest_config", return_value=config or self.CONFIG),
            patch("app.digest.get_digest_candidates", return_value=candidates) as get_candidates,
            patch(
                "app.digest.is_duplicate_story",
                side_effect=is_duplicate or (lambda candidate, known: False),
            ),
        ):
            self.get_candidates = get_candidates
            return [article["relevance_score"] for article in build_digest()["articles"]]

    def test_strong_day_never_uses_articles_below_the_cutoff(self):
        self.assertEqual(self._build([90, 85, 80, 75, 72, 71, 69, 65]), [90, 85, 80, 75, 72])
        self.get_candidates.assert_called_once_with(lookback_hours=24, minimum_score=60)

    def test_three_above_the_cutoff_are_not_topped_up(self):
        self.assertEqual(self._build([90, 80, 70, 69, 68]), [90, 80, 70])

    def test_weak_day_is_topped_up_only_to_the_floor(self):
        self.assertEqual(self._build([75, 69, 66, 64, 61]), [75, 69, 66])

    def test_day_with_nothing_above_the_cutoff_still_has_an_edition(self):
        self.assertEqual(self._build([68, 65, 63, 61]), [68, 65, 63])

    def test_floor_never_goes_below_the_fallback_score(self):
        self.assertEqual(self._build([75, 62]), [75, 62])
        self.assertEqual(self._build([]), [])

    def test_top_up_skips_duplicates_and_tries_the_next_one(self):
        scores = self._build(
            [75, 69, 66, 64],
            is_duplicate=lambda candidate, known: candidate["relevance_score"] == 69,
        )

        self.assertEqual(scores, [75, 66, 64])

    def test_without_floor_config_the_cutoff_is_strict(self):
        config = {"lookback_hours": 24, "minimum_score": 70, "maximum_articles": 5}

        self.assertEqual(self._build([75, 72], config), [75, 72])
        self.get_candidates.assert_called_once_with(lookback_hours=24, minimum_score=70)


class RecentlyDeliveredQueryTestCase(unittest.TestCase):
    def test_returns_only_stories_inside_the_window(self):
        now = datetime.now(timezone.utc)

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(db, "DB_PATH", Path(tmp) / "digest.db"),
        ):
            db.init_db()

            for title, days_ago in (("Two days ago", 2), ("Four days ago", 4), ("Pending", None)):
                url = f"https://example.com/{title}"
                db.save_article(
                    {
                        "source": "Example",
                        "title": title,
                        "url": url,
                        "published_at": now.isoformat(),
                        "feed_excerpt": f"Excerpt of {title}",
                    }
                )

                if days_ago is not None:
                    db.mark_articles_delivered(
                        [db.get_article_by_url(url)["id"]],
                        delivered_at=now - timedelta(days=days_ago),
                    )

            recent = db.get_recently_delivered(3)

        self.assertEqual(
            recent,
            [{"title": "Two days ago", "feed_excerpt": "Excerpt of Two days ago"}],
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

        self.assertIn(">tech digest</p>", html)
        self.assertIn("Formal methods with Hillel Wayne", html)
        self.assertIn("The last six months in LLMs, in five minutes", html)
        self.assertIn("What changed in SQLite this year", html)
        self.assertIn(
            "https://digest.joaoac.com/unsubscribe/preview",
            html,
        )
        self.assertNotIn("Read article", html)
        self.assertIn("today&#x27;s edition", html)


if __name__ == "__main__":
    unittest.main()
