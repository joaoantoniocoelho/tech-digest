import os
import runpy
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app.digest import render_text_digest
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
                "why_interesting": ("AI agents · Developer tools"),
                "source": "Example Feed",
                "topics": ["Compiler design"],
                "url": "https://example.com/a?b=1&c=2",
                "published_at": ("2026-09-22T15:00:00+00:00"),
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
            "#f5f5f7",
            "#ffffff",
            "#1d1d1f",
            "#424245",
            "#6e6e73",
            "#86868b",
            "#e5e5ea",
        ):
            self.assertIn(color, html)
        for old_color in ("#f4f4f5", "#18181b", "#1f6a88", "#64a9ca"):
            self.assertNotIn(old_color, html)
        self.assertIn('bgcolor="#f5f5f7"', html)
        self.assertIn('bgcolor="#ffffff"', html)
        self.assertIn("border-radius:24px", html)
        self.assertIn("Helvetica Neue", html)
        self.assertNotIn("Georgia", html)
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertIn(">tech digest</p>", html)
        self.assertIn(">today&#x27;s edition</h1>", html)
        self.assertIn(
            "tuesday, september 22, 2026 · 1 article from the last 24 hours",
            html,
        )
        self.assertIn('bgcolor="#1d1d1f"', html)
        self.assertIn(">sep 22</td>", html)
        self.assertIn(">01</td>", html)
        self.assertIn("border-top:1px solid #e5e5ea;border-bottom:1px solid #e5e5ea;", html)
        self.assertIn("tech digest, by joão coelho", html)
        self.assertIn("Tom &amp; Jerry &lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn(
            'href="https://example.com/a?b=1&amp;c=2"',
            html,
        )
        self.assertIn(
            "Example Feed · sep 22 · ai agents · developer tools · compiler design · score 87",
            html,
        )
        self.assertIn('href="https://x.com/joaoac_"', html)
        self.assertIn('href="https://joaoac.com"', html)
        self.assertNotIn("unsubscribe", html)

    def test_light_only_without_dark_styles(self):
        html = render_html_digest(digest=_digest(), sent_at=SENT_AT)

        self.assertIn('name="color-scheme" content="light"', html)
        self.assertIn('name="supported-color-schemes" content="light"', html)
        self.assertNotIn("prefers-color-scheme", html)
        self.assertNotIn("light dark", html)

    def test_copy_is_lowercase(self):
        html = render_html_digest(
            digest=_digest(),
            sent_at=SENT_AT,
            unsubscribe_url="https://digest.joaoac.com/unsubscribe/tok",
        )

        for label in (
            ">share this digest</p>",
            "know someone who would like this edition? send it their way.",
            ">link to this edition</a>",
            ">linkedin</a>",
            ">website</a>",
            ">unsubscribe</a>",
        ):
            self.assertIn(label, html)
        for old_label in ("TECH DIGEST", "Curated by", "Share this digest", "Unsubscribe"):
            self.assertNotIn(old_label, html)

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
        self.assertIn("2 articles from the last 24 hours", html)
        self.assertIn("Example Feed · sep 22</p>", html)
        self.assertIn(">Second</h2>", html)
        self.assertIn(">02</td>", html)
        self.assertNotIn(">Second</a>", html)

    def test_subject_uses_send_date(self):
        self.assertEqual(
            digest_subject(SENT_AT),
            "João Coelho Tech Digest — Sep 22, 2026",
        )


class ShareDigestTestCase(unittest.TestCase):
    def test_html_links_to_utc_edition_with_refs(self):
        html = render_html_digest(digest=_digest(), sent_at=SENT_AT)

        self.assertIn("share this digest", html)
        edition = "https://digest.joaoac.com/digest/2026-09-22"
        self.assertIn(f'href="{edition}?ref=share"', html)
        self.assertIn(
            'href="https://x.com/intent/post?text=Tech+Digest%2C+'
            "September+22%2C+2026%3A+a+short+list+of+software+engineering"
            "+articles+worth+reading.&amp;url=https%3A%2F%2Fdigest.joaoac.com"
            '%2Fdigest%2F2026-09-22%3Fref%3Dx"',
            html,
        )
        self.assertIn(
            'href="https://www.linkedin.com/sharing/share-offsite/?url='
            "https%3A%2F%2Fdigest.joaoac.com%2Fdigest%2F2026-09-22"
            '%3Fref%3Dlinkedin"',
            html,
        )

    def test_late_local_send_uses_next_utc_date(self):
        late = datetime(2026, 9, 22, 22, 30, tzinfo=timezone(timedelta(hours=-3)))

        html = render_html_digest(digest=_digest(), sent_at=late)

        self.assertIn("/digest/2026-09-23?ref=share", html)
        self.assertIn("September+23%2C+2026", html)

    def test_uses_configured_site_url(self):
        with patch.dict(os.environ, {"DIGEST_SITE_URL": "http://localhost:3000/"}):
            html = render_html_digest(digest=_digest(), sent_at=SENT_AT)

        self.assertIn('href="http://localhost:3000/digest/2026-09-22?ref=share"', html)

    def test_empty_digest_has_no_share_block(self):
        html = render_html_digest(
            digest={"lookback_hours": 24, "articles": []},
            sent_at=SENT_AT,
        )

        self.assertNotIn("share this digest", html)

    def test_text_lists_share_links(self):
        text = render_text_digest(digest=_digest(), sent_at=SENT_AT)

        self.assertIn(
            "share this digest\n"
            "know someone who would like this edition? send it their way.\n"
            "link to this edition: "
            "https://digest.joaoac.com/digest/2026-09-22?ref=share\n"
            "X: https://x.com/intent/post?text=Tech+Digest",
            text,
        )
        self.assertIn(
            "LinkedIn: https://www.linkedin.com/sharing/share-offsite/?url="
            "https%3A%2F%2Fdigest.joaoac.com%2Fdigest%2F2026-09-22"
            "%3Fref%3Dlinkedin",
            text,
        )

    def test_text_without_send_time_has_no_share_block(self):
        self.assertNotIn("share this digest", render_text_digest(digest=_digest()))


class SendEmailTestCase(unittest.TestCase):
    def test_posts_html_and_text_to_resend(self):
        response = MagicMock()
        response.is_success = True
        response.json.return_value = {"id": "email_123"}

        with patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "re_test",
                "RESEND_TO": ("reader@example.com, other@example.com"),
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
        with (
            patch.dict(
                os.environ,
                {
                    "RESEND_API_KEY": "",
                    "RESEND_TO": "reader@example.com",
                },
                clear=False,
            ),
            self.assertRaises(RuntimeError) as missing_key,
        ):
            send_email(
                subject="S",
                html_body="<p>Hi</p>",
                text="Hi",
            )

        self.assertEqual(
            str(missing_key.exception),
            "RESEND_API_KEY is not configured",
        )

        with (
            patch.dict(
                os.environ,
                {
                    "RESEND_API_KEY": "re_test",
                    "RESEND_TO": "  ",
                },
                clear=False,
            ),
            self.assertRaises(RuntimeError) as missing_to,
        ):
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

        with (
            patch.dict(
                os.environ,
                {
                    "RESEND_API_KEY": "re_test",
                    "RESEND_TO": "reader@example.com",
                },
                clear=False,
            ),
            patch(
                "app.email.httpx.post",
                return_value=response,
            ),
            self.assertRaises(RuntimeError) as error,
        ):
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
    @patch("app.send_digest.revalidate_published_edition")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.list_active_subscribers")
    @patch("app.send_digest.build_digest")
    def test_delivery_uses_email(
        self,
        build_digest,
        subscribers,
        send_email_mock,
        revalidate,
        mark,
    ):
        build_digest.return_value = self._digest()
        subscribers.return_value = [
            self._subscriber(
                "reader@example.com",
                "token-reader-1",
            )
        ]
        send_email_mock.return_value = {"id": "email_1"}

        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
        ):
            send_daily_digest()

        send_email_mock.assert_called_once()
        kwargs = send_email_mock.call_args.kwargs
        self.assertEqual(kwargs["to"], ["reader@example.com"])
        self.assertTrue(kwargs["subject"].startswith("João Coelho Tech Digest — "))
        self.assertIn("Example", kwargs["html_body"])
        self.assertIn("joão coelho", kwargs["html_body"])
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
        mark.assert_called_once()
        self.assertEqual(mark.call_args.kwargs["article_ids"], [7])
        delivered_at = mark.call_args.kwargs["delivered_at"]
        edition = (
            "https://digest.joaoac.com/digest/"
            + delivered_at.astimezone(timezone.utc).date().isoformat()
        )
        self.assertIn(edition + "?ref=share", kwargs["html_body"])
        self.assertIn(
            f"link to this edition: {edition}?ref=share",
            kwargs["text"],
        )
        revalidate.assert_called_once_with()

    @patch("app.send_digest.mark_articles_delivered")
    @patch("app.send_digest.revalidate_published_edition")
    @patch("app.send_digest.send_email")
    @patch("app.send_digest.list_active_subscribers")
    @patch("app.send_digest.build_digest")
    def test_publishes_edition_after_first_successful_send(
        self,
        build_digest,
        subscribers,
        send_email_mock,
        revalidate,
        mark,
    ):
        build_digest.return_value = self._digest()
        subscribers.return_value = [
            self._subscriber("one@example.com", "token-one-aaaa"),
            self._subscriber("two@example.com", "token-two-bbbb"),
            self._subscriber("three@example.com", "token-three-cc"),
        ]
        published_before_send = []

        def send(**kwargs):
            published_before_send.append((mark.call_count, revalidate.call_count))
            if kwargs["to"] == ["one@example.com"]:
                raise RuntimeError("Resend API error (422): bad")
            return {"id": "email"}

        send_email_mock.side_effect = send

        with (
            patch.dict(
                os.environ,
                {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
            ),
            redirect_stdout(StringIO()),
        ):
            send_daily_digest()

        self.assertEqual(published_before_send, [(0, 0), (0, 0), (1, 1)])
        mark.assert_called_once()
        revalidate.assert_called_once_with()

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
        subscribers.return_value = [self._subscriber("reader@example.com", "token-reader-1")]
        send_email_mock.side_effect = RuntimeError("Resend API error (401): invalid")

        with (
            patch.dict(
                os.environ,
                {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
            ),
            self.assertRaises(RuntimeError),
        ):
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

        with (
            patch.dict(
                os.environ,
                {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
            ),
            redirect_stdout(StringIO()) as output,
        ):
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
        mark.assert_called_once()
        self.assertEqual(mark.call_args.kwargs["article_ids"], [7])
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

        with (
            patch.dict(
                os.environ,
                {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"},
            ),
            self.assertRaises(RuntimeError),
        ):
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
            self.assertFalse(line.strip().startswith("send_message("))
