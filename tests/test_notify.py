import json
import runpy
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.log import redact_text
from app.notify import (
    notify,
    notify_job_finish,
    notify_job_start,
    notify_subscriber,
)
from app.pipeline import run_job_blocking
from app.server import create_server, rate_limiter
from app.subscribers import subscribe_email, unsubscribe_with_token


class NotifyMessageTestCase(unittest.TestCase):
    def test_missing_config_does_not_call_telegram(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("app.notify.send_message") as send_message:
                notify("Busca de notícias começou")
        send_message.assert_not_called()

    def test_configured_send_is_best_effort(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "123:secret",
            "TELEGRAM_CHAT_ID": "99",
        }
        with patch.dict("os.environ", env, clear=False):
            with patch(
                "app.notify.send_message",
                side_effect=RuntimeError("telegram down"),
            ):
                notify("Classificação começou")

    def test_job_and_subscriber_messages(self):
        sent = []

        def capture(text):
            sent.append(text)

        with patch("app.notify.notify", side_effect=capture):
            notify_job_start("collect")
            notify_job_finish(
                "collect",
                "ok",
                new_articles=4,
            )
            notify_job_start("process")
            notify_job_finish(
                "process",
                "ok",
                eligible=10,
                processed=8,
                failed=2,
            )
            notify_job_start("send")
            notify_job_finish(
                "send",
                "error",
                error="RuntimeError: mailbox down",
            )
            notify_subscriber("created", "reader@example.com")
            notify_subscriber("reactivated", "reader@example.com")
            notify_subscriber("unsubscribed", "reader@example.com")

        self.assertEqual(
            sent,
            [
                "Busca de notícias começou",
                "Busca de notícias ok · 4 novas",
                "Classificação começou",
                "Classificação ok · 10 elegíveis · 8 classificadas · 2 falhas",
                "Envio do digest começou",
                "Envio do digest erro · RuntimeError: mailbox down",
                "Cadastro · r***@example.com",
                "Cadastro reativado · r***@example.com",
                "Descadastro · r***@example.com",
            ],
        )

    def test_bot_token_is_redacted(self):
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "123:super-secret"},
            clear=False,
        ):
            self.assertNotIn(
                "super-secret",
                redact_text("token 123:super-secret leaked"),
            )


class JobAlertTestCase(unittest.TestCase):
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

    def test_job_start_and_finish_notify(self):
        calls = []

        def fake_job(_name):
            def run():
                return {"new_articles": 3}

            return run

        with patch("app.pipeline.get_job", side_effect=fake_job):
            with patch("app.pipeline.notify_job_start", side_effect=calls.append):
                with patch(
                    "app.pipeline.notify_job_finish",
                    side_effect=lambda *args, **fields: calls.append((args, fields)),
                ):
                    self.assertEqual(run_job_blocking("collect"), "ok")

        self.assertEqual(calls[0], "collect")
        self.assertEqual(calls[1][0], ("collect", "ok"))
        self.assertEqual(calls[1][1]["new_articles"], 3)

    def test_subscribe_outcomes(self):
        self.assertEqual(subscribe_email("Reader@Example.com"), "created")
        self.assertEqual(subscribe_email("reader@example.com"), "unchanged")
        token = None
        with db.get_connection() as connection:
            token = connection.execute(
                "SELECT unsubscribe_token FROM subscribers"
            ).fetchone()["unsubscribe_token"]
        result = unsubscribe_with_token(token)
        self.assertEqual(result["status"], "unsubscribed")
        self.assertEqual(result["email"], "reader@example.com")
        again = unsubscribe_with_token(token)
        self.assertEqual(again["status"], "unchanged")
        self.assertEqual(subscribe_email("reader@example.com"), "reactivated")


class SubscriberAlertTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temporary.close()
        self.db_path = Path(temporary.name)
        self.db_patcher = patch.object(db, "DB_PATH", self.db_path)
        self.db_patcher.start()
        rate_limiter.reset()
        self.server = create_server("127.0.0.1", 0)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="test-notify-http",
            daemon=True,
        )
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.db_patcher.stop()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)

    def post_json(self, path, payload):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            path,
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        status = response.status
        response.read()
        connection.close()
        return status

    def test_http_notifies_signup_once_and_unsubscribe(self):
        alerts = []
        with patch(
            "app.server.notify_subscriber",
            side_effect=lambda action, email: alerts.append((action, email)),
        ):
            self.assertEqual(
                self.post_json("/subscribe", {"email": "Reader@Example.com"}),
                200,
            )
            self.assertEqual(
                self.post_json("/subscribe", {"email": "reader@example.com"}),
                200,
            )
            with db.get_connection() as connection:
                token = connection.execute(
                    "SELECT unsubscribe_token FROM subscribers"
                ).fetchone()["unsubscribe_token"]
            self.assertEqual(self.post_json(f"/unsubscribe/{token}", {}), 200)
            self.assertEqual(self.post_json(f"/unsubscribe/{token}", {}), 200)
            self.assertEqual(
                self.post_json("/subscribe", {"email": "reader@example.com"}),
                200,
            )

        self.assertEqual(
            alerts,
            [
                ("created", "reader@example.com"),
                ("unsubscribed", "reader@example.com"),
                ("reactivated", "reader@example.com"),
            ],
        )
