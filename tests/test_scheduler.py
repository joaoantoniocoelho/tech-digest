import runpy
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.pipeline import (
    acquire_pipeline_lock,
    release_pipeline_lock,
    run_job_blocking,
)
from app.scheduler import ZONE, Scheduler, due_slots, slot_key, slot_status


def _at(hour, minute):
    return datetime(2026, 9, 23, hour, minute, tzinfo=ZONE)


class ScheduleTestCase(unittest.TestCase):
    def test_slots_follow_sao_paulo_cadence(self):
        self.assertEqual(
            [(job, when.hour, when.minute) for when, job in due_slots(_at(4, 10))],
            [("collect", 4, 0)],
        )
        self.assertEqual(
            [(job, when.hour, when.minute) for when, job in due_slots(_at(8, 10))],
            [("process", 6, 50), ("send", 7, 0), ("collect", 8, 0)],
        )
        self.assertEqual(
            [(job, when.hour, when.minute) for when, job in due_slots(_at(6, 47))],
            [("collect", 6, 45)],
        )
        self.assertEqual(
            [(job, when.hour, when.minute) for when, job in due_slots(_at(6, 55))],
            [("collect", 6, 45), ("process", 6, 50)],
        )
        self.assertEqual(
            [(job, when.hour, when.minute) for when, job in due_slots(_at(7, 20))],
            [("process", 6, 50), ("send", 7, 0)],
        )
        self.assertEqual(
            [(job, when.hour, when.minute) for when, job in due_slots(_at(7, 10))],
            [("collect", 6, 45), ("process", 6, 50), ("send", 7, 0)],
        )
        self.assertEqual(due_slots(_at(10, 0)), [])

    def test_slot_key_uses_sao_paulo(self):
        scheduled = _at(7, 0)
        self.assertEqual(
            slot_key("send", scheduled),
            "send:2026-09-23T07:00:00-03:00",
        )


class PipelineLockTestCase(unittest.TestCase):
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

    def test_lock_blocks_until_release_and_recovers_when_stale(self):
        self.assertTrue(acquire_pipeline_lock("collect", "owner-a"))
        self.assertFalse(acquire_pipeline_lock("process", "owner-b"))
        release_pipeline_lock("someone-else")
        self.assertFalse(acquire_pipeline_lock("process", "owner-b"))
        release_pipeline_lock("owner-a")
        self.assertTrue(acquire_pipeline_lock("process", "owner-b"))
        release_pipeline_lock("owner-b")

        self.assertTrue(acquire_pipeline_lock("collect", "owner-a"))
        with db.get_connection() as connection:
            connection.execute(
                """
                UPDATE pipeline_lock
                SET heartbeat_at = ?
                WHERE owner = ?
                """,
                ("2000-01-01T00:00:00+00:00", "owner-a"),
            )
        self.assertTrue(acquire_pipeline_lock("send", "owner-c"))
        release_pipeline_lock("owner-c")

    def test_running_job_blocks_another_and_failure_does_not_stick(self):
        started = threading.Event()
        release = threading.Event()

        def fake_job(_name):
            def run():
                started.set()
                self.assertTrue(release.wait(2))
                if _name == "process":
                    raise RuntimeError("classifier down")
                return {"new_articles": 3}
            return run

        with patch("app.pipeline.get_job", side_effect=fake_job):
            worker = threading.Thread(
                target=run_job_blocking,
                args=("collect",),
            )
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertEqual(run_job_blocking("process"), "busy")
            release.set()
            worker.join(timeout=2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(run_job_blocking("process"), "error")
            self.assertEqual(run_job_blocking("collect"), "ok")


class SchedulerLoopTestCase(unittest.TestCase):
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

    def test_tick_runs_earliest_due_job_once(self):
        calls = []

        def fake_run(job):
            calls.append(job)
            return "ok"

        scheduler = Scheduler()
        with patch("app.scheduler.run_job_blocking", side_effect=fake_run):
            scheduler._tick(_at(6, 55))
            scheduler._tick(_at(6, 55))
            scheduler._tick(_at(6, 56))

        self.assertEqual(calls, ["collect", "process"])
        self.assertEqual(
            slot_status(slot_key("collect", _at(6, 45))),
            "ok",
        )
        self.assertEqual(slot_status(slot_key("process", _at(6, 50))), "ok")

    def test_locked_job_is_retried_and_errors_are_contained(self):
        scheduler = Scheduler(tick_seconds=0)
        outcomes = iter(["busy", "error"])

        def fake_run(_job):
            return next(outcomes)

        with patch("app.scheduler.run_job_blocking", side_effect=fake_run):
            with patch("app.scheduler.due_slots", return_value=[(_at(7, 0), "send")]):
                scheduler._tick(_at(7, 0))
                self.assertIsNone(slot_status(slot_key("send", _at(7, 0))))
                scheduler._tick(_at(7, 0))
                self.assertEqual(slot_status(slot_key("send", _at(7, 0))), "error")

        calls = {"n": 0}

        def exploding(_now=None):
            calls["n"] += 1
            if calls["n"] >= 2:
                scheduler.stop()
                return
            raise RuntimeError("scheduler blew up")

        with patch.object(scheduler, "_tick", side_effect=exploding):
            scheduler._loop()

        self.assertGreaterEqual(calls["n"], 2)
