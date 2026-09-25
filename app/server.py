import hashlib
import hmac
import html
import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app.db import DB_PATH, get_connection, init_db
from app.log import log_event, redact_text
from app.pipeline import start_job
from app.scheduler import Scheduler
from app.subscribers import (
    subscribe_email,
    token_is_known,
    unsubscribe_with_token,
)
from app.welcome import queue_welcome_email


MAX_BODY_BYTES = 4096
_JOBS = ("collect", "process", "send")
_DEFAULT_ORIGINS = (
    "https://digest.joaoac.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
)


class RateLimiter:
    def __init__(
        self,
        limit: int = 5,
        window_seconds: int = 600,
        global_limit: int = 60,
    ):
        self.limit = limit
        self.window_seconds = window_seconds
        self.global_limit = global_limit
        self._hits = {}
        self._global = []
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._global.clear()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self.window_seconds

        with self._lock:
            self._global = [
                stamp
                for stamp in self._global
                if now - stamp < window
            ]
            hits = [
                stamp
                for stamp in self._hits.get(key, [])
                if now - stamp < window
            ]
            if (
                len(hits) >= self.limit
                or len(self._global) >= self.global_limit
            ):
                self._hits[key] = hits
                return False

            hits.append(now)
            self._global.append(now)
            self._hits[key] = hits
            if len(self._hits) > 5000:
                self._hits = {
                    item_key: item_hits
                    for item_key, item_hits in self._hits.items()
                    if item_hits
                }
            return True


rate_limiter = RateLimiter()


def allowed_origins() -> set[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return set(_DEFAULT_ORIGINS)
    return {
        item.strip()
        for item in raw.split(",")
        if item.strip()
    }


def listen_port() -> int:
    raw = os.getenv("PORT", "8080").strip()
    try:
        port = int(raw)
    except ValueError as error:
        raise RuntimeError("PORT is not a valid integer") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT is not a valid integer")
    return port


def _tokens_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def _database_ok() -> bool:
    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return True
    except Exception as error:
        log_event(
            "health_error",
            error=redact_text(
                f"{type(error).__name__}: {error}"
            ),
        )
        return False


def _page(title: str, message: str, token: str | None = None) -> str:
    form = ""
    if token:
        action = html.escape(f"/unsubscribe/{token}", quote=True)
        form = (
            f'<form method="post" action="{action}">'
            '<button type="submit">Unsubscribe</button>'
            "</form>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
</head>
<body style="margin:0;background:#000;color:#f4f4f5;font-family:sans-serif;">
<main style="max-width:32rem;margin:4rem auto;padding:0 1.25rem;">
<p style="letter-spacing:0.14em;color:#64a9ca;">TECH DIGEST</p>
<h1 style="font-weight:600;">{html.escape(title)}</h1>
<p style="color:#d4d4d8;line-height:1.5;">{html.escape(message)}</p>
{form}
</main>
</body>
</html>
"""


class DigestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def version_string(self) -> str:
        return "tech-digest"

    def log_message(self, fmt, *args) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_OPTIONS(self) -> None:
        self._dispatch("OPTIONS")

    def _dispatch(self, method: str) -> None:
        self.close_connection = True
        self._responded = False
        try:
            self._route(method)
        except Exception as error:
            log_event(
                "http_error",
                error=redact_text(
                    f"{type(error).__name__}: {error}"
                ),
            )
            if not self._responded:
                self._reply(500, {"ok": False})

    def _route(self, method: str) -> None:
        path = self._request_path()

        if method == "OPTIONS":
            self._reply(204, b"")
            return

        if path == "/health" and method == "GET":
            if _database_ok():
                self._reply(200, {"ok": True})
            else:
                self._reply(503, {"ok": False})
            return

        if path == "/subscribe" and method == "POST":
            self._subscribe()
            return

        if path.startswith("/unsubscribe/"):
            token = path.removeprefix("/unsubscribe/")
            if "/" in token or not token:
                self._reply(404, {"ok": False})
                return
            if method == "GET":
                self._unsubscribe_page(token)
                return
            if method == "POST":
                self._unsubscribe(token)
                return

        if path.startswith("/jobs/"):
            job = path.removeprefix("/jobs/")
            if method == "POST" and job in _JOBS:
                self._start_job(job)
                return

        self._reply(404, {"ok": False})

    def _subscribe(self) -> None:
        if not rate_limiter.allow(self._client_ip()):
            self._reply(429, {"ok": False})
            return

        payload, status = self._read_json()
        if status is not None:
            self._reply(status, {"ok": False})
            return

        try:
            subscriber = subscribe_email(payload.get("email"))
        except ValueError:
            self._reply(400, {"ok": False})
            return

        self._reply(200, {"ok": True})
        if subscriber:
            queue_welcome_email(subscriber)

    def _unsubscribe_page(self, token: str) -> None:
        if not token_is_known(token):
            self._reply(
                404,
                _page(
                    "Link not valid",
                    "This unsubscribe link is not valid.",
                ),
                content_type="text/html; charset=utf-8",
            )
            return

        self._reply(
            200,
            _page(
                "Unsubscribe",
                "Confirm that you want to stop receiving Tech Digest.",
                token=token,
            ),
            content_type="text/html; charset=utf-8",
        )

    def _unsubscribe(self, token: str) -> None:
        if not unsubscribe_with_token(token):
            self._reply(
                404,
                _page(
                    "Link not valid",
                    "This unsubscribe link is not valid.",
                ),
                content_type="text/html; charset=utf-8",
            )
            return

        self._reply(
            200,
            _page(
                "Unsubscribed",
                "You will no longer receive Tech Digest.",
            ),
            content_type="text/html; charset=utf-8",
        )

    def _start_job(self, job: str) -> None:
        authorization = _job_authorization(
            self.headers.get("Authorization", "")
        )
        if authorization == "disabled":
            self._reply(404, {"ok": False})
            return
        if authorization != "ok":
            self._reply(401, {"ok": False})
            return

        try:
            outcome = start_job(job)
        except KeyError:
            self._reply(404, {"ok": False})
            return

        if outcome == "busy":
            self._reply(409, {"ok": False})
            return

        self._reply(202, {"ok": True, "job": job})

    def _read_json(self):
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None, 400

        try:
            length = int(raw_length)
        except ValueError:
            return None, 400

        if length < 0 or length > MAX_BODY_BYTES:
            return None, 413

        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            return None, 400

        try:
            payload = json.loads(
                self.rfile.read(length).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, 400

        if not isinstance(payload, dict):
            return None, 400

        return payload, None

    def _request_path(self) -> str:
        path = urlsplit(self.path).path or "/"
        if len(path) > 1:
            path = path.rstrip("/")
        return path

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        parts = [
            part.strip()
            for part in forwarded.split(",")
            if part.strip()
        ]
        if parts:
            return parts[-1][:128]
        host = self.client_address[0] if self.client_address else ""
        return host

    def _write_cors(self) -> None:
        self.send_header("Vary", "Origin")
        origin = self.headers.get("Origin", "")
        if origin in allowed_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, OPTIONS",
            )
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization",
            )
            self.send_header("Access-Control-Max-Age", "600")

    def _reply(
        self,
        status: int,
        payload,
        content_type: str = "application/json; charset=utf-8",
    ) -> None:
        if isinstance(payload, bytes):
            body = payload
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload).encode("utf-8")

        self._responded = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self._write_cors()
        self.end_headers()
        if body:
            self.wfile.write(body)
        self._access(status)

    def _access(self, status: int) -> None:
        path = self._request_path()
        if path.startswith("/unsubscribe/"):
            path = "/unsubscribe"
        log_event(
            "http",
            method=self.command,
            path=path,
            status=status,
        )


def _job_authorization(header: str) -> str:
    expected = os.getenv("DIGEST_JOB_TOKEN", "").strip()
    if not expected:
        return "disabled"
    if not header.startswith("Bearer "):
        return "unauthorized"
    provided = header.removeprefix("Bearer ").strip()
    if not provided or not _tokens_match(provided, expected):
        return "unauthorized"
    return "ok"


def create_server(host: str = "0.0.0.0", port: int | None = None):
    init_db()
    listen = listen_port() if port is None else port
    return ThreadingHTTPServer((host, listen), DigestHandler)


def main() -> None:
    port = listen_port()
    server = create_server("0.0.0.0", port)
    log_event(
        "server_start",
        host="0.0.0.0",
        port=port,
        db_path=str(DB_PATH),
    )
    scheduler = Scheduler()
    scheduler.start()

    def stop(signum, _frame) -> None:
        log_event("shutdown", signal=signum)
        scheduler.stop()
        threading.Thread(
            target=server.shutdown,
            name="http-shutdown",
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        log_event("server_stop")


if __name__ == "__main__":
    main()
