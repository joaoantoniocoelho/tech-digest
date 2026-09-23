import json
import runpy
import tempfile
import threading
import time
import unittest
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
            body=b'{"email":"%s@example.com"}' % (b"a" * 5000),
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
