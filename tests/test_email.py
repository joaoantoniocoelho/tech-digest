import os
import runpy
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app.email import (
    DEFAULT_FROM,
    digest_subject,
    render_html_digest,
    send_email,
)
from app.send_digest import send_daily_digest


SENT_AT = datetime(
    2026,
    9,
    22,
    18,
    54,
    tzinfo=timezone(timedelta(hours=-3)),
)


def _digest():
    return {
        "lookback_hours": 24,
        "articles": [
            {
                "id": 7,
                "title": "Tom & Jerry <script>",
                "relevance_score": 87,
                "why_interesting": (
                    "AI agents · Developer tools"
                ),
                "source": "Example Feed",
                "topics": ["Compiler design"],
                "url": "https://example.com/a?b=1&c=2",
                "published_at": (
                    "2026-09-22T15:00:00+00:00"
                ),
            }
        ],
    }


class RenderHtmlDigestTestCase(unittest.TestCase):
    def test_theme_and_article_content(self):
        html = render_html_digest(
            digest=_digest(),
            show_score=True,
            show_topics=True,
            sent_at=SENT_AT,
        )

        for color in (
            "#000000",
            "#f4f4f5",
            "#d4d4d8",
            "#a1a1aa",
            "#262626",
            "#7dd3fc",
            "#fde68a",
            "#4a8aa8",
            "#64a9ca",
        ):
            self.assertIn(color, html)
        self.assertIn("background-color:#f4f4f5", html)
        self.assertIn('bgcolor="#f4f4f5"', html)
        dark_css = html.split(
            "@media (prefers-color-scheme: dark)",
            1,
        )[1]
        self.assertIn(
            "background-color:#000000 !important",
            dark_css,
        )
        body = html.split("<body", 1)[1]
        self.assertIn("background-color:#f4f4f5", body)
        self.assertNotIn("background-color:#000000", body)
        self.assertIn("IBM Plex Sans", html)
        self.assertIn("IBM Plex Mono", html)
        self.assertIn(
            "https://fonts.googleapis.com/css2?family="
            "IBM+Plex+Sans:ital,wght@0,100..700;1,100..700"
            "&amp;display=swap",
            html,
        )
        self.assertIn(
            'rel="preconnect" href="https://fonts.googleapis.com"',
            html,
        )
        self.assertIn(
            'href="https://fonts.gstatic.com" crossorigin',
            html,
        )
        self.assertIn(
            "font-variation-settings:'wdth' 100",
            html,
        )
        self.assertIn("font-optical-sizing:auto", html)
        self.assertNotIn("border-radius:", html)
        self.assertNotIn("mix-blend-mode", html)
        self.assertIn("João Coelho", html)
        self.assertIn("Tuesday, Sep 22, 2026", html)
        self.assertIn("1 article · last 24 hours", html)
        self.assertIn(
            ">Tom &amp; Jerry &lt;script&gt;</a>",
            html,
        )
        self.assertNotIn("<script>", html)
        self.assertIn(
            "https://example.com/a?b=1&amp;c=2",
            html,
        )
        self.assertIn("Example Feed", html)
        self.assertIn("Compiler design", html)
        self.assertIn("Score 87", html)
        self.assertIn("AI agents · Developer tools", html)
        self.assertIn("Sep 22", html)
        self.assertIn('href="https://x.com/joaoac_dev"', html)
        self.assertIn('href="https://joaoac.com"', html)
        self.assertIn("original publisher", html)
        self.assertNotIn("Unsubscribe", html)

    def test_theme_follows_client_color_scheme(self):
        html = render_html_digest(
            digest=_digest(),
            show_score=True,
            show_topics=True,
            sent_at=SENT_AT,
        )

        self.assertIn(
            'name="color-scheme" content="light dark"',
            html,
        )
        self.assertIn(
            'name="supported-color-schemes" content="light dark"',
            html,
        )
        self.assertIn(
            "color-scheme: light dark",
            html,
        )
        self.assertIn(
            ".dm-bg { background-color:#000000 !important; }",
            html,
        )
        self.assertIn(
            ".dm-fg { color:#f4f4f5 !important; }",
            html,
        )
        self.assertIn(
            ".dm-link { color:#fde68a !important; }",
            html,
        )
        self.assertIn(
            ".dm-mark { color:#7dd3fc !important; }",
            html,
        )
        self.assertNotIn("mix-blend-mode", html)
        self.assertNotIn("gmail-blend", html)

    def test_hides_score_topics_and_empty_why(self):
        digest = _digest()
        digest["articles"][0]["why_interesting"] = "   "
        digest["articles"].append(
            {
                "title": "Second",
                "relevance_score": 70,
                "why_interesting": "",
                "source": "Other",
                "topics": ["Databases"],
                "url": "not-a-url",
                "published_at": "not a date",
            }
        )

        html = render_html_digest(
            digest=digest,
            show_score=False,
            show_topics=False,
            sent_at=SENT_AT,
        )

        self.assertNotIn("Score 87", html)
        self.assertNotIn("Compiler design", html)
        self.assertNotIn("Databases", html)
        self.assertNotIn(">Why<", html)
        self.assertIn("2 articles · last 24 hours", html)
        self.assertIn(">Second</h2>", html)
        self.assertNotIn(">Second</a>", html)

    def test_subject_uses_send_date(self):
        self.assertEqual(
            digest_subject(SENT_AT),
            "Tech Digest — Sep 22, 2026",
        )


class SendEmailTestCase(unittest.TestCase):
    def test_posts_html_and_text_to_resend(self):
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {
            "id": "email_123"
        }

        with patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "re_test",
                "RESEND_TO": (
                    "reader@example.com, "
                    "other@example.com"
                ),
            },
            clear=False,
        ):
            os.environ.pop("RESEND_FROM", None)

            with patch(
                "app.email.httpx.post",
                return_value=response,
            ) as post:
                result = send_email(
                    subject="Tech Digest — Sep 22, 2026",
                    html_body="<p>Hi</p>",
                    text="Hi",
                )

        self.assertEqual(result["id"], "email_123")

        args, kwargs = post.call_args

        self.assertEqual(
            args[0],
            "https://api.resend.com/emails",
        )
        self.assertEqual(
            kwargs["headers"]["Authorization"],
            "Bearer re_test",
        )
        self.assertEqual(
            kwargs["json"]["from"],
            DEFAULT_FROM,
        )
        self.assertEqual(
            kwargs["json"]["to"],
            [
                "reader@example.com",
                "other@example.com",
            ],
        )
        self.assertEqual(
            kwargs["json"]["html"],
            "<p>Hi</p>",
        )
        self.assertEqual(
            kwargs["json"]["text"],
            "Hi",
        )

    def test_missing_key_and_recipient(self):
        with patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "",
                "RESEND_TO": "reader@example.com",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as missing_key:
                send_email(
                    subject="S",
                    html_body="<p>Hi</p>",
                    text="Hi",
                )

        self.assertEqual(
            str(missing_key.exception),
            "RESEND_API_KEY is not configured",
        )

        with patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "re_test",
                "RESEND_TO": "  ",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as missing_to:
                send_email(
                    subject="S",
                    html_body="<p>Hi</p>",
                    text="Hi",
                )

        self.assertEqual(
            str(missing_to.exception),
            "RESEND_TO is not configured",
        )

    def test_api_error_raises(self):
        response = MagicMock()
        response.is_success = False
        response.status_code = 422
        response.text = "Invalid `from` field"

        with patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "re_test",
                "RESEND_TO": "reader@example.com",
            },
            clear=False,
        ):
            with patch(
                "app.email.httpx.post",
                return_value=response,
            ):
                with self.assertRaises(RuntimeError) as error:
                    send_email(
                        subject="S",
                        html_body="<p>Hi</p>",
                        text="Hi",
                    )

        self.assertIn("422", str(error.exception))
        self.assertIn(
            "Invalid `from` field",
            str(error.exception),
        )


class SendDailyDigestTestCase(unittest.TestCase):
    def _article(self):
        return {
            "id": 7,
            "title": "Example",
            "relevance_score": 80,
            "why_interesting": "AI agents",
            "source": "Test",
            "topics": [],
            "url": "https://example.com",
            "published_at": None,
        }

    def _subscriber(self, email, token):
        return {
            "id": 1,
            "email": email,
            "unsubscribe_token": token,
        }

    def _digest(self):
        return {
            "lookback_hours": 24,
            "articles": [self._article()],
        }

    @patch("app.send_digest.mark_articles_delivered")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.list_active_subscribers")
    @patch("app.send_digest.build_digest")
    def test_delivery_uses_email(
        self,
        build_digest,
        subscribers,
        send_email_mock,
        mark,
    ):
        build_digest.return_value = self._digest()
        subscribers.return_value = [
            self._subscriber(
                "reader@example.com",
                "token-reader-1",
            )
        ]
        send_email_mock.return_value = {
            "id": "email_1"
        }

        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
        ):
            send_daily_digest()

        send_email_mock.assert_called_once()
        kwargs = send_email_mock.call_args.kwargs
        self.assertEqual(kwargs["to"], ["reader@example.com"])
        self.assertTrue(
            kwargs["subject"].startswith(
                "Tech Digest — "
            )
        )
        self.assertIn("Example", kwargs["html_body"])
        self.assertIn("João Coelho", kwargs["html_body"])
        self.assertIn(
            "https://api.digest.joaoac.com/unsubscribe/token-reader-1",
            kwargs["html_body"],
        )
        self.assertIn(
            "Why: AI agents",
            kwargs["text"],
        )
        self.assertIn(
            "Unsubscribe: https://api.digest.joaoac.com/unsubscribe/token-reader-1",
            kwargs["text"],
        )
        mark.assert_called_once_with(
            article_ids=[7],
        )

    @patch("app.send_digest.mark_articles_delivered")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.list_active_subscribers")
    @patch("app.send_digest.build_digest")
    def test_failed_send_does_not_mark_delivered(
        self,
        build_digest,
        subscribers,
        send_email_mock,
        mark,
    ):
        build_digest.return_value = self._digest()
        subscribers.return_value = [
            self._subscriber("reader@example.com", "token-reader-1")
        ]
        send_email_mock.side_effect = RuntimeError(
            "Resend API error (401): invalid"
        )

        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
        ):
            with self.assertRaises(RuntimeError):
                send_daily_digest()

        mark.assert_not_called()

    @patch("app.send_digest.mark_articles_delivered")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.list_active_subscribers")
    @patch("app.send_digest.build_digest")
    def test_one_recipient_failure_does_not_block_others(
        self,
        build_digest,
        subscribers,
        send_email_mock,
        mark,
    ):
        build_digest.return_value = self._digest()
        subscribers.return_value = [
            self._subscriber("one@example.com", "token-one-aaaa"),
            self._subscriber("two@example.com", "token-two-bbbb"),
        ]
        send_email_mock.side_effect = [
            RuntimeError("Resend API error (422): bad one@example.com"),
            {"id": "email_2"},
        ]

        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
        ):
            with redirect_stdout(StringIO()) as output:
                send_daily_digest()

        self.assertEqual(send_email_mock.call_count, 2)
        first = send_email_mock.call_args_list[0].kwargs
        second = send_email_mock.call_args_list[1].kwargs
        self.assertEqual(first["to"], ["one@example.com"])
        self.assertEqual(second["to"], ["two@example.com"])
        self.assertIn("token-one-aaaa", first["html_body"])
        self.assertIn("token-two-bbbb", second["html_body"])
        self.assertNotIn("two@example.com", first["html_body"])
        self.assertNotIn("one@example.com", second["html_body"])
        mark.assert_called_once_with(article_ids=[7])
        logged = output.getvalue()
        self.assertNotIn("token-one-aaaa", logged)
        self.assertNotIn("token-two-bbbb", logged)
        self.assertNotIn("one@example.com", logged)
        self.assertIn("o***@example.com", logged)
        self.assertIn("t***@example.com", logged)

    @patch("app.send_digest.mark_articles_delivered")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.list_active_subscribers")
    @patch("app.send_digest.build_digest")
    def test_all_recipient_failures_do_not_mark_delivered(
        self,
        build_digest,
        subscribers,
        send_email_mock,
        mark,
    ):
        build_digest.return_value = self._digest()
        subscribers.return_value = [
            self._subscriber("one@example.com", "token-one-aaaa"),
            self._subscriber("two@example.com", "token-two-bbbb"),
        ]
        send_email_mock.side_effect = RuntimeError("Resend down")

        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
        ):
            with self.assertRaises(RuntimeError):
                send_daily_digest()

        self.assertEqual(send_email_mock.call_count, 2)
        mark.assert_not_called()

    @patch("app.send_digest.mark_articles_delivered")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.list_active_subscribers")
    @patch("app.send_digest.build_digest")
    def test_zero_subscribers_does_not_fail(
        self,
        build_digest,
        subscribers,
        send_email_mock,
        mark,
    ):
        build_digest.return_value = self._digest()
        subscribers.return_value = []

        with redirect_stdout(StringIO()) as output:
            result = send_daily_digest()

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["subscribers"], 0)
        self.assertIn("No active subscribers", output.getvalue())
        send_email_mock.assert_not_called()
        mark.assert_not_called()

    @patch("app.send_digest.mark_articles_delivered")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.build_digest")
    def test_empty_digest_does_not_send(
        self,
        build_digest,
        send_email_mock,
        mark,
    ):
        build_digest.return_value = {
            "lookback_hours": 24,
            "articles": [],
        }

        send_daily_digest()

        send_email_mock.assert_not_called()
        mark.assert_not_called()

    def test_telegram_call_stays_commented(self):
        source = Path("app/send_digest.py").read_text()

        self.assertIn("# send_message(", source)

        for line in source.splitlines():
            self.assertFalse(
                line.strip().startswith(
                    "send_message("
                )
            )
