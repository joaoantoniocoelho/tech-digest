import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

from app.db import get_connection, init_db
from app.log import log_event, redact_text


STALE_LOCK_SECONDS = 90
HEARTBEAT_SECONDS = 20

_PUBLIC_RESULT_KEYS = (
    "new_articles",
    "processed",
    "failed",
    "articles",
    "subscribers",
    "sent",
    "failed_sends",
)


def _owner() -> str:
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def acquire_pipeline_lock(job: str, owner: str) -> bool:
    init_db()
    now = _utc_now()
    stale_before = _isoformat(
        now - timedelta(seconds=STALE_LOCK_SECONDS)
    )
    current = _isoformat(now)

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT heartbeat_at
            FROM pipeline_lock
            WHERE id = 1
            """
        ).fetchone()

        if (
            row is not None
            and row["heartbeat_at"] >= stale_before
        ):
            return False

        connection.execute(
            """
            INSERT INTO pipeline_lock (
                id,
                job,
                owner,
                acquired_at,
                heartbeat_at
            )
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                job = excluded.job,
                owner = excluded.owner,
                acquired_at = excluded.acquired_at,
                heartbeat_at = excluded.heartbeat_at
            """,
            (job, owner, current, current),
        )

    return True


def heartbeat_pipeline_lock(owner: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE pipeline_lock
            SET heartbeat_at = ?
            WHERE id = 1
              AND owner = ?
            """,
            (_isoformat(_utc_now()), owner),
        )


def release_pipeline_lock(owner: str) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM pipeline_lock
            WHERE id = 1
              AND owner = ?
            """,
            (owner,),
        )


def lock_is_fresh() -> bool:
    stale_before = _isoformat(
        _utc_now() - timedelta(seconds=STALE_LOCK_SECONDS)
    )
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT heartbeat_at
            FROM pipeline_lock
            WHERE id = 1
            """
        ).fetchone()

    return (
        row is not None
        and row["heartbeat_at"] >= stale_before
    )


def get_job(name: str):
    if name == "collect":
        from app.main import run_collect

        return run_collect

    if name == "process":
        from app.process_daily import run_process_job

        return run_process_job

    if name == "send":
        from app.send_digest import send_daily_digest

        return send_daily_digest

    raise KeyError(name)


def _public_fields(result) -> dict:
    if not isinstance(result, dict):
        return {}

    fields = {}
    for key in _PUBLIC_RESULT_KEYS:
        value = result.get(key)
        if isinstance(value, int):
            fields[key] = value
    return fields


def _pulse(owner: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            heartbeat_pipeline_lock(owner)
        except Exception as error:
            log_event(
                "job_heartbeat_error",
                error=redact_text(
                    f"{type(error).__name__}: {error}"
                ),
            )


def _run_held(job: str, owner: str, function) -> str:
    stop = threading.Event()
    pulse = threading.Thread(
        target=_pulse,
        args=(owner, stop),
        name=f"heartbeat-{job}",
        daemon=True,
    )
    pulse.start()

    try:
        log_event("job_start", job=job)
        result = function()
        log_event(
            "job_finish",
            job=job,
            status="ok",
            **_public_fields(result),
        )
        return "ok"
    except Exception as error:
        log_event(
            "job_finish",
            job=job,
            status="error",
            error=redact_text(
                f"{type(error).__name__}: {error}"
            ),
        )
        return "error"
    finally:
        stop.set()
        release_pipeline_lock(owner)


def run_job_blocking(job: str) -> str:
    function = get_job(job)
    owner = _owner()
    if not acquire_pipeline_lock(job, owner):
        return "busy"
    return _run_held(job, owner, function)


def start_job(job: str) -> str:
    function = get_job(job)
    owner = _owner()
    if not acquire_pipeline_lock(job, owner):
        return "busy"

    threading.Thread(
        target=_run_held,
        args=(job, owner, function),
        name=f"job-{job}",
        daemon=True,
    ).start()
    return "started"


def run_cli(job: str) -> None:
    outcome = run_job_blocking(job)
    if outcome == "busy":
        log_event("job_skip", job=job, reason="locked")
        raise SystemExit(1)
    if outcome == "error":
        raise SystemExit(1)
