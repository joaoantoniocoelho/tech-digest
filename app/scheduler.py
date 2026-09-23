import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.db import get_connection, init_db
from app.log import log_event, redact_text
from app.pipeline import run_job_blocking


TIMEZONE_NAME = "America/Sao_Paulo"
ZONE = ZoneInfo(TIMEZONE_NAME)
TICK_SECONDS = 20

# (job, hour, minute, grace)
_RULES = [
    *[
        ("collect", hour, 0, timedelta(minutes=30))
        for hour in range(0, 24, 4)
    ],
    ("collect", 6, 45, timedelta(minutes=30)),
    ("process", 6, 50, timedelta(hours=3)),
    ("send", 7, 0, timedelta(hours=3)),
]


def due_slots(now: datetime) -> list[tuple[datetime, str]]:
    local = now.astimezone(ZONE)
    found = []

    for job, hour, minute, grace in _RULES:
        scheduled = local.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if scheduled <= local < scheduled + grace:
            found.append((scheduled, job))

    found.sort(key=lambda item: (item[0], item[1]))
    return found


def slot_key(job: str, scheduled: datetime) -> str:
    local = scheduled.astimezone(ZONE)
    return f"{job}:{local.isoformat(timespec='seconds')}"


def slot_status(slot: str) -> str | None:
    init_db()
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT status
            FROM job_runs
            WHERE slot = ?
            """,
            (slot,),
        ).fetchone()

    if row is None:
        return None
    return row["status"]


def slot_needs_run(slot: str) -> bool:
    return slot_status(slot) not in {"ok", "error"}


def record_slot(slot: str, job: str, status: str) -> None:
    finished_at = datetime.now(ZONE).isoformat(timespec="seconds")
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO job_runs (
                slot,
                job,
                status,
                finished_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(slot) DO UPDATE SET
                job = excluded.job,
                status = excluded.status,
                finished_at = excluded.finished_at
            """,
            (slot, job, status, finished_at),
        )


class Scheduler:
    def __init__(self, tick_seconds: float = TICK_SECONDS):
        self._stop = threading.Event()
        self._thread = None
        self._tick_seconds = tick_seconds

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        log_event("scheduler_start", timezone=TIMEZONE_NAME)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as error:
                log_event(
                    "scheduler_error",
                    error=redact_text(
                        f"{type(error).__name__}: {error}"
                    ),
                )
            self._stop.wait(self._tick_seconds)

    def _tick(self, now: datetime | None = None) -> None:
        current = now or datetime.now(ZONE)

        for scheduled, job in due_slots(current):
            slot = slot_key(job, scheduled)
            if not slot_needs_run(slot):
                continue

            outcome = run_job_blocking(job)
            if outcome == "busy":
                log_event(
                    "job_deferred",
                    job=job,
                    slot=slot,
                    reason="locked",
                )
                return

            record_slot(
                slot,
                job,
                "ok" if outcome == "ok" else "error",
            )
            return
