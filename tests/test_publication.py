import os
import unittest
from unittest.mock import Mock, patch

import httpx

from app.publication import revalidate_published_edition


class PublicationTestCase(unittest.TestCase):
    @patch("app.publication.httpx.post")
    @patch("app.publication.list_public_editions")
    def test_revalidates_new_and_previous_editions(
        self, editions: Mock, post: Mock
    ) -> None:
        editions.return_value = [
            {"date": "2026-10-05", "article_count": 8},
            {"date": "2026-10-04", "article_count": 8},
        ]
        post.return_value = Mock()

        with patch.dict(
            os.environ,
            {
                "DIGEST_REVALIDATE_TOKEN": "test-secret",
                "DIGEST_SITE_URL": "https://digest.example.com/",
            },
        ):
            self.assertTrue(revalidate_published_edition())

        post.assert_called_once_with(
            "https://digest.example.com/api/revalidate",
            headers={"Authorization": "Bearer test-secret"},
            json={"date": "2026-10-05", "previousDate": "2026-10-04"},
            timeout=5.0,
        )

    @patch("app.publication.httpx.post")
    @patch("app.publication.list_public_editions")
    def test_failure_is_retried_without_failing_delivery(
        self, editions: Mock, post: Mock
    ) -> None:
        editions.return_value = [{"date": "2026-10-05", "article_count": 8}]
        post.side_effect = httpx.ConnectError("unavailable")

        with patch.dict(os.environ, {"DIGEST_REVALIDATE_TOKEN": "test-secret"}):
            self.assertFalse(revalidate_published_edition())

        self.assertEqual(post.call_count, 3)

    @patch("app.publication.httpx.post")
    def test_missing_token_disables_revalidation(self, post: Mock) -> None:
        with patch.dict(os.environ, {"DIGEST_REVALIDATE_TOKEN": ""}):
            self.assertFalse(revalidate_published_edition())

        post.assert_not_called()
