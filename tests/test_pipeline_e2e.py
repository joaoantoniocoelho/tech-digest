import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.pipeline import run_job_blocking
from app.subscribers import subscribe_email

FEED_URL = "https://feeds.example.com/tech.xml"

MODEL_LAUNCH = "OpenAI releases GPT-7 with agentic tool use"
DATABASE_RELEASE = "PostgreSQL 19 ships a new open source query planner"
MODEL_LAUNCH_REPEAT = "GPT-7 is here: what OpenAI's new model can do"
GADGET_REVIEW = "Hands-on with the new Galaxy earbuds"
OLD_STORY = "A story from last week"

# Raw Jev scores (0-2 per feature) the fake model returns for each title.
FEATURE_SCORES = {
    MODEL_LAUNCH: {
        "major_ai_model_development": 2.0,
        "ai_agents": 2.0,
        "ai_assisted_software_engineering": 2.0,
    },
    DATABASE_RELEASE: {
        "databases_developer_infrastructure": 2.0,
        "open_source": 2.0,
        "developer_tools": 2.0,
    },
    MODEL_LAUNCH_REPEAT: {"major_ai_model_development": 2.0},
    GADGET_REVIEW: {"gadget_review": 2.0},
}
IMPORTANCE_SCORES = {
    MODEL_LAUNCH: 3.0,
    DATABASE_RELEASE: 2.0,
    MODEL_LAUNCH_REPEAT: 1.0,
    GADGET_REVIEW: 0.0,
}
SAME_STORY = {MODEL_LAUNCH, MODEL_LAUNCH_REPEAT}


def _rss_item(title: str, published_at: datetime) -> str:
    slug = "-".join(title.lower().split())[:40]
    return (
        "<item>"
        f"<title>{title}</title>"
        f"<link>https://news.example.com/{slug}</link>"
        f"<pubDate>{format_datetime(published_at)}</pubDate>"
        f"<description>Excerpt for {title}</description>"
        "</item>"
    )


def _feed() -> bytes:
    now = datetime.now(timezone.utc)
    items = [
        _rss_item(MODEL_LAUNCH, now - timedelta(hours=2)),
        _rss_item(DATABASE_RELEASE, now - timedelta(hours=3)),
        _rss_item(MODEL_LAUNCH_REPEAT, now - timedelta(hours=4)),
        _rss_item(GADGET_REVIEW, now - timedelta(hours=5)),
        _rss_item(OLD_STORY, now - timedelta(days=5)),
    ]
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<rss version="2.0"><channel><title>Example tech</title>'
        f"{''.join(items)}"
        "</channel></rss>"
    ).encode()


class FakeJev:
    """Stands in for the TypeSafe client: answers by article title, never by network."""

    def __init__(self):
        self.classified_titles = []

    def system_one(self, state, questions, model):
        if "duplicate" in questions:
            titles = {state["candidate"]["title"]}
            titles |= {story["title"] for story in state["selected_stories"]}
            score = 2.0 if SAME_STORY.issubset(titles) else 0.0
            return SimpleNamespace(scores={"duplicate": SimpleNamespace(score=score)})

        title = state["title"]
        self.classified_titles.append(title)
        features = FEATURE_SCORES.get(title, {})
        scores = {
            question_id: SimpleNamespace(score=features.get(question_id, 0.0))
            for question_id in questions
        }
        scores["importance"] = SimpleNamespace(score=IMPORTANCE_SCORES.get(title, 0.0))
        return SimpleNamespace(scores=scores)


class FakeResend:
    def __init__(self):
        self.payloads = []

    def post(self, url, headers, json, timeout):
        self.payloads.append(json)
        return SimpleNamespace(
            is_success=True,
            status_code=200,
            text="",
            json=lambda: {"id": f"email_{len(self.payloads)}"},
        )


class DailyPipelineEndToEndTestCase(unittest.TestCase):
    """Collect -> classify -> score -> build digest -> send, with only the network faked."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)

        sources = root / "sources.yaml"
        sources.write_text(f"sources:\n  - name: Example tech\n    url: {FEED_URL}\n")

        self.jev = FakeJev()
        self.resend = FakeResend()

        def download_feed(name, url):
            return _feed() if url == FEED_URL else None

        patchers = [
            # clear=True keeps a developer's real TELEGRAM_*/DIGEST_* settings out of the run.
            patch.dict(
                os.environ,
                {
                    "TYPESAFE_API_KEY": "test-key",
                    "RESEND_API_KEY": "test-resend-key",
                    "PUBLIC_BASE_URL": "https://api.digest.example.com",
                },
                clear=True,
            ),
            patch.object(db, "DB_PATH", root / "digest.db"),
            patch("app.main.Path", return_value=sources),
            patch("app.rss._download_feed", side_effect=download_feed),
            patch(
                "app.processor.fetch_article_content",
                side_effect=lambda url: f"Full article text from {url}. " * 20,
            ),
            patch("app.classifier._get_client", return_value=self.jev),
            patch("app.email.httpx.post", side_effect=self.resend.post),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

        db.init_db()
        subscribe_email("reader@example.com")

    def _run(self, job):
        with redirect_stdout(StringIO()):
            return run_job_blocking(job)

    def _article(self, title):
        with db.get_connection() as connection:
            return connection.execute(
                "SELECT * FROM articles WHERE title = ?",
                (title,),
            ).fetchone()

    def test_daily_pipeline_delivers_ranked_digest_by_email(self):
        self.assertEqual(self._run("collect"), "ok")
        self.assertEqual(self._run("process"), "ok")
        self.assertEqual(self._run("send"), "ok")

        # Only articles inside the processing window reach the model.
        self.assertCountEqual(
            self.jev.classified_titles,
            [MODEL_LAUNCH, DATABASE_RELEASE, MODEL_LAUNCH_REPEAT, GADGET_REVIEW],
        )
        self.assertIsNone(self._article(OLD_STORY)["processed_at"])

        scores = {title: self._article(title)["relevance_score"] for title in FEATURE_SCORES}
        self.assertLess(scores[GADGET_REVIEW], 60)
        selected = sorted(
            [MODEL_LAUNCH, DATABASE_RELEASE],
            key=lambda title: scores[title],
            reverse=True,
        )

        self.assertEqual(len(self.resend.payloads), 1)
        email = self.resend.payloads[0]
        self.assertEqual(email["to"], ["reader@example.com"])
        self.assertTrue(email["subject"].startswith("João Coelho Tech Digest — "))

        for body in (email["html"], email["text"]):
            self.assertIn(MODEL_LAUNCH, body)
            self.assertIn(DATABASE_RELEASE, body)
            self.assertNotIn(MODEL_LAUNCH_REPEAT, body)
            self.assertNotIn(GADGET_REVIEW, body)
            self.assertNotIn(OLD_STORY, body)
            self.assertLess(body.index(selected[0]), body.index(selected[1]))

        self.assertIn("https://api.digest.example.com/unsubscribe/", email["text"])

        self.assertIsNotNone(self._article(MODEL_LAUNCH)["delivered_at"])
        self.assertIsNotNone(self._article(DATABASE_RELEASE)["delivered_at"])
        self.assertIsNone(self._article(MODEL_LAUNCH_REPEAT)["delivered_at"])
        self.assertIsNone(self._article(GADGET_REVIEW)["delivered_at"])

    def test_next_cycle_does_not_resend_delivered_articles(self):
        for job in ("collect", "process", "send"):
            self._run(job)
        with db.get_connection() as connection:
            collected = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

        for job in ("collect", "process", "send"):
            self.assertEqual(self._run(job), "ok")

        with db.get_connection() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                collected,
            )
        self.assertEqual(self.jev.classified_titles.count(MODEL_LAUNCH), 1)
        for email in self.resend.payloads[1:]:
            self.assertNotIn(MODEL_LAUNCH, email["text"])
            self.assertNotIn(DATABASE_RELEASE, email["text"])


if __name__ == "__main__":
    unittest.main()
