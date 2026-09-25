import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.digest import render_text_welcome
from app.email import WELCOME_SUBJECT, render_html_welcome
from app.welcome import send_welcome_email


SENT_AT = datetime(
    2026,
    9,
    25,
    9,
    30,
    tzinfo=timezone(timedelta(hours=-3)),
)


def _edition():
    return {
        "delivered_at": datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc),
        "lookback_hours": 24,
        "articles": [
            {
                "id": 1,
                "title": "Tom & Jerry <script>",
                "url": "https://example.com/a?b=1&c=2",
                "source": "Example Feed",
                "published_at": "2026-09-24T08:00:00+00:00",
                "why_interesting": "AI agents",
                "relevance_score": 90,
                "topics": [],
            },
            {
                "id": 2,
                "title": "Second story",
                "url": "https://example.com/second",
                "source": "Other Feed",
                "published_at": None,
                "why_interesting": "Databases",
                "relevance_score": 70,
                "topics": [],
            },
        ],
    }


class LatestEditionTestCase(unittest.TestCase):
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

    def _insert(self, title, score, delivered_at):
        with db.get_connection() as connection:
            connection.execute(
                """
                INSERT INTO articles (
                    source, title, url, relevance_score,
                    why_interesting, topics, processed_at, delivered_at
                )
                VALUES ('Feed', ?, ?, ?, 'AI agents', '["AI"]',
                        '2026-09-24 09:00:00', ?)
                """,
                (title, f"https://example.com/{title}", score, delivered_at),
            )

    def test_returns_none_without_deliveries(self):
        self._insert("pending", 90, None)
        self.assertIsNone(db.get_latest_edition())

    def test_returns_only_most_recent_batch_by_score(self):
        self._insert("old", 99, "2026-09-23 10:00:00")
        self._insert("low", 65, "2026-09-24 10:00:05")
        self._insert("high", 88, "2026-09-24 10:00:05")
        self._insert("pending", 95, None)

        edition = db.get_latest_edition()

        self.assertEqual(
            [article["title"] for article in edition["articles"]],
            ["high", "low"],
        )
        self.assertEqual(edition["articles"][0]["topics"], ["AI"])
        self.assertEqual(
            edition["delivered_at"],
            datetime(2026, 9, 24, 10, 0, 5, tzinfo=timezone.utc),
        )


class RenderWelcomeTestCase(unittest.TestCase):
    def test_html_includes_welcome_and_latest_edition(self):
        html = render_html_welcome(
            edition=_edition(),
            sent_at=SENT_AT,
            unsubscribe_url="https://digest.joaoac.com/unsubscribe/tok",
        )

        self.assertIn("Welcome to Tech Digest.", html)
        self.assertIn("Thanks for subscribing.", html)
        self.assertIn("here is the latest edition", html)
        self.assertIn("Latest edition · Thursday, September 24, 2026", html)
        self.assertIn("2 selected stories from the last 24 hours", html)
        self.assertIn("Tom &amp; Jerry &lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("https://example.com/a?b=1&amp;c=2", html)
        self.assertIn("Second story", html)
        self.assertIn(">WELCOME<", html)
        self.assertIn(
            'href="https://digest.joaoac.com/unsubscribe/tok"',
            html,
        )
        self.assertIn("prefers-color-scheme: dark", html)

    def test_html_without_edition(self):
        html = render_html_welcome(edition=None, sent_at=SENT_AT)

        self.assertIn("Welcome to Tech Digest.", html)
        self.assertIn("Your first edition arrives with the next morning send.", html)
        self.assertNotIn("Latest edition", html)
        self.assertNotIn("Unsubscribe", html)

    def test_text_includes_latest_edition(self):
        text = render_text_welcome(
            edition=_edition(),
            unsubscribe_url="https://digest.joaoac.com/unsubscribe/tok",
        )

        self.assertTrue(text.startswith("Welcome to João Coelho Tech Digest"))
        self.assertIn("here is the latest edition", text)
        self.assertIn("1. Tom & Jerry <script>", text)
        self.assertIn("2. Second story", text)
        self.assertNotIn("[90]", text)
        self.assertEqual(text.count("Unsubscribe:"), 1)
        self.assertTrue(
            text.endswith("Unsubscribe: https://digest.joaoac.com/unsubscribe/tok")
        )

    def test_text_without_edition(self):
        text = render_text_welcome(
            edition={"articles": []},
            unsubscribe_url="https://digest.joaoac.com/unsubscribe/tok",
        )

        self.assertIn("Your first edition arrives with the next morning send.", text)
        self.assertIn("Unsubscribe: https://digest.joaoac.com/unsubscribe/tok", text)


class SendWelcomeEmailTestCase(unittest.TestCase):
    def _subscriber(self):
        return {
            "email": "reader@example.com",
            "unsubscribe_token": "token-reader-welcome-1",
        }

    @patch("app.welcome.send_email")
    @patch("app.welcome.get_latest_edition")
    def test_sends_welcome_with_latest_edition(self, latest, send_email_mock):
        latest.return_value = _edition()
        send_email_mock.return_value = {"id": "email_1"}

        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://digest.joaoac.com"},
        ):
            with redirect_stdout(StringIO()) as output:
                self.assertTrue(send_welcome_email(self._subscriber()))

        kwargs = send_email_mock.call_args.kwargs
        self.assertEqual(kwargs["to"], ["reader@example.com"])
        self.assertEqual(kwargs["subject"], WELCOME_SUBJECT)
        self.assertIn("Second story", kwargs["html_body"])
        self.assertIn("Second story", kwargs["text"])
        self.assertIn(
            "https://digest.joaoac.com/unsubscribe/token-reader-welcome-1",
            kwargs["html_body"],
        )
        logged = output.getvalue()
        self.assertIn("event=welcome_send status=ok", logged)
        self.assertIn("articles=2", logged)
        self.assertNotIn("reader@example.com", logged)
        self.assertNotIn("token-reader-welcome-1", logged)

    @patch("app.welcome.send_email")
    @patch("app.welcome.get_latest_edition")
    def test_failure_is_logged_and_redacted(self, latest, send_email_mock):
        latest.return_value = None
        send_email_mock.side_effect = RuntimeError(
            "Resend API error (422): bad reader@example.com"
        )

        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://digest.joaoac.com"},
        ):
            with redirect_stdout(StringIO()) as output:
                self.assertFalse(send_welcome_email(self._subscriber()))

        logged = output.getvalue()
        self.assertIn("event=welcome_send status=error", logged)
        self.assertNotIn("reader@example.com", logged)


if __name__ == "__main__":
    unittest.main()
