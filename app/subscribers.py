import os
import re
import secrets
from datetime import datetime, timezone

from app.db import get_connection


_LOCAL_PART = (
    r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
)
_DOMAIN = (
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)
_EMAIL_RE = re.compile(rf"^{_LOCAL_PART}@{_DOMAIN}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


def normalize_email(value) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid email")

    email = value.strip().lower()
    if (
        not email
        or len(email) > 254
        or _EMAIL_RE.fullmatch(email) is None
    ):
        raise ValueError("invalid email")

    return email


def public_base_url() -> str:
    value = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if (
        not value.startswith(("https://", "http://"))
        or any(character.isspace() for character in value)
    ):
        raise RuntimeError("PUBLIC_BASE_URL is not configured")
    return value


def unsubscribe_url(token: str) -> str:
    return f"{public_base_url()}/unsubscribe/{token}"


def subscribe_email(raw_email) -> str:
    email = normalize_email(raw_email)
    now = datetime.now(timezone.utc).isoformat()
    token = secrets.token_urlsafe(32)

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id, status
            FROM subscribers
            WHERE email = ?
            """,
            (email,),
        ).fetchone()

        if row is None:
            connection.execute(
                """
                INSERT INTO subscribers (
                    email,
                    status,
                    subscribed_at,
                    unsubscribed_at,
                    unsubscribe_token
                )
                VALUES (?, 'active', ?, NULL, ?)
                """,
                (email, now, token),
            )
            return "created"

        if row["status"] != "active":
            connection.execute(
                """
                UPDATE subscribers
                SET status = 'active',
                    subscribed_at = ?,
                    unsubscribed_at = NULL
                WHERE id = ?
                """,
                (now, row["id"]),
            )
            return "reactivated"

        return "unchanged"


def token_is_known(token: str) -> bool:
    if _TOKEN_RE.fullmatch(token or "") is None:
        return False

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM subscribers
            WHERE unsubscribe_token = ?
            """,
            (token,),
        ).fetchone()

    return row is not None


def unsubscribe_with_token(token: str) -> dict | None:
    if _TOKEN_RE.fullmatch(token or "") is None:
        return None

    now = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id, email, status
            FROM subscribers
            WHERE unsubscribe_token = ?
            """,
            (token,),
        ).fetchone()

        if row is None:
            return None

        status = "unchanged"
        if row["status"] != "unsubscribed":
            connection.execute(
                """
                UPDATE subscribers
                SET status = 'unsubscribed',
                    unsubscribed_at = ?
                WHERE id = ?
                """,
                (now, row["id"]),
            )
            status = "unsubscribed"

        return {
            "email": row["email"],
            "status": status,
        }


def list_active_subscribers() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, email, unsubscribe_token
            FROM subscribers
            WHERE status = 'active'
            ORDER BY id
            """
        ).fetchall()

    return [
        {
            "id": row["id"],
            "email": row["email"],
            "unsubscribe_token": row["unsubscribe_token"],
        }
        for row in rows
    ]
