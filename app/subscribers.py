import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
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

# Long values are truncated rather than rejected: attribution is best-effort
# metadata and must never make an otherwise valid signup fail.
_SOURCE_MAX_LENGTH = 64
_URL_MAX_LENGTH = 2048
_UTM_MAX_LENGTH = 128


@dataclass(frozen=True)
class Attribution:
    acquisition_source: str | None = None
    acquisition_url: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None


def _clean_attribution_value(
    value: object,
    max_length: int,
    lowercase: bool = False,
) -> str | None:
    if not isinstance(value, str):
        return None

    if lowercase:
        # Lowercasing can lengthen a string ("İ" -> "i̇"), so it happens
        # before truncation to keep the max length guarantee.
        value = value.lower()

    # Dropping non-printable characters also removes lone surrogates, which
    # json.loads accepts but sqlite3 cannot encode (that would be a 500).
    printable = "".join(
        character for character in value if character.isprintable()
    )
    cleaned = printable.strip()[:max_length].strip()
    return cleaned or None


def normalize_attribution(payload: Mapping[str, object]) -> Attribution:
    return Attribution(
        acquisition_source=_clean_attribution_value(
            payload.get("acquisition_source"),
            _SOURCE_MAX_LENGTH,
            lowercase=True,
        ),
        acquisition_url=_clean_attribution_value(
            payload.get("acquisition_url"),
            _URL_MAX_LENGTH,
        ),
        utm_source=_clean_attribution_value(
            payload.get("utm_source"),
            _UTM_MAX_LENGTH,
        ),
        utm_medium=_clean_attribution_value(
            payload.get("utm_medium"),
            _UTM_MAX_LENGTH,
        ),
        utm_campaign=_clean_attribution_value(
            payload.get("utm_campaign"),
            _UTM_MAX_LENGTH,
        ),
    )


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


def subscribe_email(
    raw_email,
    attribution: Attribution | None = None,
) -> dict | None:
    email = normalize_email(raw_email)
    attribution = attribution or Attribution()
    now = datetime.now(timezone.utc).isoformat()
    token = secrets.token_urlsafe(32)

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id, status, unsubscribe_token
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
                    unsubscribe_token,
                    created_at,
                    acquisition_source,
                    acquisition_url,
                    utm_source,
                    utm_medium,
                    utm_campaign
                )
                VALUES (?, 'active', ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    email,
                    now,
                    token,
                    now,
                    attribution.acquisition_source,
                    attribution.acquisition_url,
                    attribution.utm_source,
                    attribution.utm_medium,
                    attribution.utm_campaign,
                ),
            )
            return {
                "email": email,
                "unsubscribe_token": token,
                "action": "created",
            }

        # Attribution and created_at are first-touch: they are written only
        # when the row is created, so repeat signups and reactivations never
        # overwrite them.
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
            return {
                "email": email,
                "unsubscribe_token": row["unsubscribe_token"],
                "action": "reactivated",
            }

    return None


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
