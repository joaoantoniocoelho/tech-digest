import json
import runpy
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.server import create_server, rate_limiter
from app.subscribers import subscribe_email


class HttpApiTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temporary.close()
        self.db_path = Path(temporary.name)
        self.db_patcher = patch.object(db, "DB_PATH", self.db_path)
        self.db_patcher.start()
        rate_limiter.reset()
        self.env = patch.dict(
            "os.environ",
            {"DIGEST_JOB_TOKEN": ""},
            clear=False,
        )
        self.env.start()
        self.welcome_patcher = patch("app.server.queue_welcome_email")
        self.queue_welcome = self.welcome_patcher.start()
        self.server = create_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="test-http",
            daemon=True,
        )
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.welcome_patcher.stop()
        self.env.stop()
        self.db_patcher.stop()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return status, response_headers, payload

    def post_json(self, path, payload, headers=None):
        merged = {"Content-Type": "application/json"}
        if headers:
            merged.update(headers)
        return self.request(
            "POST",
            path,
            body=json.dumps(payload).encode(),
            headers=merged,
        )

    def test_health_is_minimal(self):
        status, _headers, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        self.assertNotIn(b"subscriber", payload.lower())
        self.assertNotIn(b"digest.db", payload)

    def test_public_editions_include_delivered_articles_only(self):
        with db.get_connection() as connection:
            connection.executemany(
                """
                INSERT INTO articles (
                    source, title, url, relevance_score, why_interesting,
                    topics, delivered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "Example",
                        "First story",
                        "https://example.com/first",
                        90,
                        "Useful context",
                        '["Developer tools"]',
                        "2026-09-29 10:00:00",
                    ),
                    (
                        "Other",
                        "Second story",
                        "https://example.com/second",
                        80,
                        "Worth reading",
                        '["Security"]',
                        "2026-09-29 10:00:00",
                    ),
                    (
                        "Example",
                        "New story",
                        "https://example.com/new",
                        70,
                        "New context",
                        '["AI research"]',
                        "2026-09-30 10:00:00",
                    ),
                    (
                        "Example",
                        "Unsent",
                        "https://example.com/unsent",
                        99,
                        "Not public yet",
                        "[]",
                        None,
                    ),
                ],
            )

        status, _headers, body = self.request("GET", "/digests")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {
                "editions": [
                    {"date": "2026-09-30", "article_count": 1},
                    {"date": "2026-09-29", "article_count": 2},
                ],
                "page": 1,
                "has_more": False,
            },
        )

        status, _headers, body = self.request("GET", "/digests/2026-09-29")
        self.assertEqual(status, 200)
        edition = json.loads(body)
        self.assertEqual(edition["date"], "2026-09-29")
        self.assertEqual(
            [article["title"] for article in edition["articles"]],
            ["First story", "Second story"],
        )
        self.assertEqual(edition["articles"][0]["topics"], ["Developer tools"])
        self.assertEqual(edition["articles"][0]["why_interesting"], "Useful context")
        self.assertNotIn("Unsent", body.decode())
        self.assertIsNone(edition["older_date"])
        self.assertEqual(edition["newer_date"], "2026-09-30")

        for path in ("/digests/2026-09-28", "/digests/2026-9-29", "/digests/nope"):
            with self.subTest(path=path):
                status, _headers, _body = self.request("GET", path)
                self.assertEqual(status, 404)

    def insert_editions(self, count):
        first = date(2026, 1, 1)
        days = [first + timedelta(days=offset) for offset in range(count)]
        with db.get_connection() as connection:
            connection.executemany(
                """
                INSERT INTO articles (source, title, url, delivered_at)
                VALUES ('Example', ?, ?, ?)
                """,
                [
                    (
                        f"Story {day.isoformat()}",
                        f"https://example.com/{day.isoformat()}",
                        f"{day.isoformat()} 10:00:00",
                    )
                    for day in days
                ],
            )
        return [day.isoformat() for day in reversed(days)]

    def get_json(self, path):
        status, _headers, body = self.request("GET", path)
        return status, json.loads(body)

    def test_public_editions_are_paginated_newest_first(self):
        expected = self.insert_editions(45)

        cases = (
            ("/digests", 1, expected[:20], True),
            ("/digests?page=1", 1, expected[:20], True),
            ("/digests?page=2", 2, expected[20:40], True),
            ("/digests?page=3", 3, expected[40:], False),
            ("/digests?page=4", 4, [], False),
        )
        for path, page, dates, has_more in cases:
            with self.subTest(path=path):
                status, payload = self.get_json(path)
                self.assertEqual(status, 200)
                self.assertEqual(payload["page"], page)
                self.assertEqual(payload["has_more"], has_more)
                self.assertEqual(
                    [edition["date"] for edition in payload["editions"]], dates
                )

    def test_exact_page_boundary_has_no_next_page(self):
        self.insert_editions(20)

        status, payload = self.get_json("/digests")

        self.assertEqual(status, 200)
        self.assertEqual(len(payload["editions"]), 20)
        self.assertFalse(payload["has_more"])

    def test_empty_archive_returns_empty_first_page(self):
        status, payload = self.get_json("/digests")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"editions": [], "page": 1, "has_more": False})

    def test_invalid_page_is_rejected(self):
        for query in (
            "page=0",
            "page=-1",
            "page=01",
            "page=1.5",
            "page=abc",
            "page=",
            "page=%C2%B2",
            "page=1&page=2",
            "page=100001",
            "page=" + "9" * 5000,
        ):
            with self.subTest(query=query[:20]):
                status, payload = self.get_json(f"/digests?{query}")
                self.assertEqual(status, 400)
                self.assertEqual(payload, {"ok": False})

    def test_edition_includes_neighbor_dates(self):
        dates = list(reversed(self.insert_editions(3)))

        cases = (
            (dates[0], None, dates[1]),
            (dates[1], dates[0], dates[2]),
            (dates[2], dates[1], None),
        )
        for edition_date, older, newer in cases:
            with self.subTest(date=edition_date):
                status, payload = self.get_json(f"/digests/{edition_date}")
                self.assertEqual(status, 200)
                self.assertEqual(payload["older_date"], older)
                self.assertEqual(payload["newer_date"], newer)

    def test_subscribe_normalizes_and_hides_duplicates(self):
        origin = {"Origin": "https://digest.joaoac.com"}
        first_status, first_headers, first_body = self.post_json(
            "/subscribe",
            {"email": "  Reader@Example.com "},
            origin,
        )
        second_status, _second_headers, second_body = self.post_json(
            "/subscribe",
            {"email": "reader@example.com"},
            origin,
        )
        self.assertEqual(first_status, 200)
        self.assertEqual(json.loads(first_body), {"ok": True})
        self.assertEqual(second_status, 200)
        self.assertEqual(json.loads(second_body), {"ok": True})
        self.assertEqual(
            first_headers.get("access-control-allow-origin"),
            "https://digest.joaoac.com",
        )
        with db.get_connection() as connection:
            rows = connection.execute(
                "SELECT email, status FROM subscribers"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "reader@example.com")
        self.assertEqual(rows[0]["status"], "active")
        self.queue_welcome.assert_called_once()
        subscriber = self.queue_welcome.call_args.args[0]
        self.assertEqual(subscriber["email"], "reader@example.com")
        self.assertGreaterEqual(len(subscriber["unsubscribe_token"]), 20)

    def _attribution_row(self, email="reader@example.com"):
        with db.get_connection() as connection:
            return dict(
                connection.execute(
                    """
                    SELECT status, acquisition_source, acquisition_url,
                           utm_source, utm_medium, utm_campaign
                    FROM subscribers
                    WHERE email = ?
                    """,
                    (email,),
                ).fetchone()
            )

    def test_subscribe_persists_acquisition_attribution(self):
        status, _headers, payload = self.post_json(
            "/subscribe",
            {
                "email": "reader@example.com",
                "acquisition_source": " X ",
                "acquisition_url": "https://joaoac.com/digest/2026-09-29?ref=x",
                "utm_source": "twitter",
                "utm_medium": "social",
                "utm_campaign": "launch",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        self.assertEqual(
            self._attribution_row(),
            {
                "status": "active",
                "acquisition_source": "x",
                "acquisition_url": "https://joaoac.com/digest/2026-09-29?ref=x",
                "utm_source": "twitter",
                "utm_medium": "social",
                "utm_campaign": "launch",
            },
        )

    def test_subscribe_without_attribution_stores_nulls(self):
        status, _headers, payload = self.post_json(
            "/subscribe",
            {"email": "reader@example.com"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        self.assertEqual(
            self._attribution_row(),
            {
                "status": "active",
                "acquisition_source": None,
                "acquisition_url": None,
                "utm_source": None,
                "utm_medium": None,
                "utm_campaign": None,
            },
        )
        self.queue_welcome.assert_called_once()

    def test_subscribe_ignores_invalid_attribution(self):
        status, _headers, payload = self.post_json(
            "/subscribe",
            {
                "email": "reader@example.com",
                "acquisition_source": 42,
                "acquisition_url": ["https://example.com"],
                "utm_source": {"nested": True},
                "utm_medium": "   ",
                "utm_campaign": "c" * 1000,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        row = self._attribution_row()
        self.assertEqual(row["status"], "active")
        self.assertIsNone(row["acquisition_source"])
        self.assertIsNone(row["acquisition_url"])
        self.assertIsNone(row["utm_source"])
        self.assertIsNone(row["utm_medium"])
        self.assertEqual(row["utm_campaign"], "c" * 128)

    def test_resubscribe_keeps_first_touch_attribution(self):
        first_status, _headers, _payload = self.post_json(
            "/subscribe",
            {"email": "reader@example.com", "acquisition_source": "shipclub"},
        )
        second_status, _headers, _payload = self.post_json(
            "/subscribe",
            {
                "email": "reader@example.com",
                "acquisition_source": "linkedin",
                "utm_campaign": "later",
            },
        )
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        row = self._attribution_row()
        self.assertEqual(row["acquisition_source"], "shipclub")
        self.assertIsNone(row["utm_campaign"])

    def test_reactivation_keeps_first_touch_attribution_and_created_at(self):
        status, _headers, _payload = self.post_json(
            "/subscribe",
            {"email": "reader@example.com", "acquisition_source": "x"},
        )
        self.assertEqual(status, 200)
        with db.get_connection() as connection:
            original = connection.execute(
                "SELECT created_at, unsubscribe_token FROM subscribers"
            ).fetchone()
            connection.execute(
                "UPDATE subscribers SET created_at = '2026-01-01T00:00:00+00:00'"
            )

        unsubscribe_status, _headers, _body = self.request(
            "POST",
            f"/unsubscribe/{original['unsubscribe_token']}",
        )
        self.assertEqual(unsubscribe_status, 200)

        status, _headers, payload = self.post_json(
            "/subscribe",
            {
                "email": "reader@example.com",
                "acquisition_source": "linkedin",
                "utm_source": "li",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        row = self._attribution_row()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["acquisition_source"], "x")
        self.assertIsNone(row["utm_source"])
        with db.get_connection() as connection:
            created_at = connection.execute(
                "SELECT created_at FROM subscribers"
            ).fetchone()["created_at"]
        self.assertEqual(created_at, "2026-01-01T00:00:00+00:00")
        self.assertEqual(self.queue_welcome.call_count, 2)

    def test_subscribe_accepts_attribution_at_maximum_lengths(self):
        local_part = "a" * 64
        domain = ".".join(["b" * 61, "c" * 61, "d" * 61, "com"])
        email = f"{local_part}@{domain}"
        self.assertEqual(len(email), 254)
        url = "https://joaoac.com/digest/" + "%C3%A9" * 400
        url = url[:2048]
        payload = {
            "email": email,
            "acquisition_source": "é" * 64,
            "acquisition_url": url,
            "utm_source": "é" * 128,
            "utm_medium": "é" * 128,
            "utm_campaign": "é" * 128,
        }
        body = json.dumps(payload).encode("utf-8")
        self.assertGreater(len(body), 4096)
        self.assertLessEqual(len(body), 8192)

        status, _headers, response = self.request(
            "POST",
            "/subscribe",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"ok": True})
        row = self._attribution_row(email)
        self.assertEqual(row["acquisition_url"], url)
        self.assertEqual(row["utm_campaign"], "é" * 128)

    def test_subscribe_drops_lone_surrogates_in_attribution(self):
        status, _headers, payload = self.request(
            "POST",
            "/subscribe",
            body=b'{"email":"reader@example.com","utm_source":"a\\ud800b"}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        self.assertEqual(self._attribution_row()["utm_source"], "ab")

    def test_invalid_email_and_body_limits(self):
        status, _headers, payload = self.post_json(
            "/subscribe",
            {"email": "not-an-email"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload), {"ok": False})

        oversized = self.request(
            "POST",
            "/subscribe",
            body=b'{"email":"%s@example.com"}' % (b"a" * 9000),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(oversized[0], 413)

    def test_subscribe_rate_limit(self):
        forwarded = {"X-Forwarded-For": "198.51.100.9, 203.0.113.10"}
        for _ in range(5):
            status, _headers, _payload = self.post_json(
                "/subscribe",
                {"email": "reader@example.com"},
                forwarded,
            )
            self.assertEqual(status, 200)
        limited, _headers, payload = self.post_json(
            "/subscribe",
            {"email": "reader@example.com"},
            forwarded,
        )
        self.assertEqual(limited, 429)
        self.assertEqual(json.loads(payload), {"ok": False})
        other, _headers, _payload = self.post_json(
            "/subscribe",
            {"email": "other@example.com"},
            {"X-Forwarded-For": "203.0.113.10, 198.51.100.20"},
        )
        self.assertEqual(other, 200)

    def test_cors_is_restricted(self):
        allowed, allowed_headers, _payload = self.request(
            "OPTIONS",
            "/subscribe",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        self.assertEqual(allowed, 204)
        self.assertEqual(
            allowed_headers.get("access-control-allow-origin"),
            "http://localhost:3000",
        )
        self.assertNotIn("*", allowed_headers.get("access-control-allow-origin", ""))

        denied, denied_headers, _payload = self.post_json(
            "/subscribe",
            {"email": "reader@example.com"},
            {"Origin": "https://evil.example"},
        )
        self.assertEqual(denied, 200)
        self.assertNotIn("access-control-allow-origin", denied_headers)

    def test_custom_cors_allowlist_replaces_defaults(self):
        with patch.dict(
            "os.environ",
            {"CORS_ALLOWED_ORIGINS": "https://digest.joaoac.com"},
            clear=False,
        ):
            status, headers, _payload = self.request(
                "OPTIONS",
                "/subscribe",
                headers={"Origin": "http://localhost:3000"},
            )
        self.assertEqual(status, 204)
        self.assertNotIn("access-control-allow-origin", headers)

    def test_unsubscribe_flow_and_invalid_token(self):
        subscribe_email("reader@example.com")
        with db.get_connection() as connection:
            token = connection.execute(
                "SELECT unsubscribe_token FROM subscribers"
            ).fetchone()["unsubscribe_token"]

        page_status, _headers, page = self.request("GET", f"/unsubscribe/{token}")
        self.assertEqual(page_status, 200)
        self.assertIn(b"Confirm that you want to stop", page)
        self.assertNotIn(token.encode(), page.split(b"action=", 1)[0])
        with db.get_connection() as connection:
            status = connection.execute(
                "SELECT status FROM subscribers"
            ).fetchone()["status"]
        self.assertEqual(status, "active")

        post_status, _headers, body = self.request(
            "POST",
            f"/unsubscribe/{token}",
        )
        self.assertEqual(post_status, 200)
        self.assertIn(b"no longer receive", body)
        with db.get_connection() as connection:
            status = connection.execute(
                "SELECT status FROM subscribers"
            ).fetchone()["status"]
        self.assertEqual(status, "unsubscribed")

        self.post_json("/subscribe", {"email": "reader@example.com"})
        with db.get_connection() as connection:
            row = connection.execute(
                "SELECT status, unsubscribe_token FROM subscribers"
            ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["unsubscribe_token"], token)

        missing_status, _headers, missing = self.request(
            "GET",
            "/unsubscribe/this-token-does-not-exist-at-all",
        )
        self.assertEqual(missing_status, 404)
        self.assertIn(b"not valid", missing)
        self.assertNotIn(b"reader@example.com", missing)

    def test_job_endpoint_auth_and_lock(self):
        from app.pipeline import (
            acquire_pipeline_lock,
            lock_is_fresh,
            release_pipeline_lock,
        )

        missing, _headers, _payload = self.request(
            "POST",
            "/jobs/collect",
            headers={"Authorization": "Bearer secret-token"},
        )
        self.assertEqual(missing, 404)

        with patch.dict("os.environ", {"DIGEST_JOB_TOKEN": "secret-token"}):
            denied, _headers, _payload = self.request(
                "POST",
                "/jobs/collect",
                headers={"Authorization": "Bearer wrong-token"},
            )
            self.assertEqual(denied, 401)

            started = threading.Event()

            def fake_job(_name):
                def run():
                    started.set()
                    return {"new_articles": 0}
                return run

            with patch("app.pipeline.get_job", side_effect=fake_job):
                accepted, _headers, payload = self.request(
                    "POST",
                    "/jobs/collect",
                    headers={"Authorization": "Bearer secret-token"},
                )
            self.assertEqual(accepted, 202)
            self.assertEqual(json.loads(payload)["job"], "collect")
            self.assertTrue(started.wait(2))
            for _ in range(50):
                if not lock_is_fresh():
                    break
                time.sleep(0.02)
            self.assertFalse(lock_is_fresh())

            owner = "test-owner"
            self.assertTrue(acquire_pipeline_lock("collect", owner))
            try:
                with patch("app.pipeline.get_job", side_effect=fake_job):
                    busy, _headers, busy_body = self.request(
                        "POST",
                        "/jobs/process",
                        headers={"Authorization": "Bearer secret-token"},
                    )
            finally:
                release_pipeline_lock(owner)
            self.assertEqual(busy, 409)
            self.assertEqual(json.loads(busy_body), {"ok": False})
