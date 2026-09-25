import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.subscribers import (
    list_active_subscribers,
    normalize_email,
    subscribe_email,
    token_is_known,
    unsubscribe_with_token,
)


class SubscriberStoreTestCase(unittest.TestCase):
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

    def _row(self, email="reader@example.com"):
        with db.get_connection() as connection:
            return connection.execute(
                """
                SELECT email, status, subscribed_at, unsubscribed_at, unsubscribe_token
                FROM subscribers
                WHERE email = ?
                """,
                (email,),
            ).fetchone()

    def test_new_subscribe_is_active(self):
        subscribe_email("  Reader@Example.com ")
        row = self._row()
        self.assertEqual(row["email"], "reader@example.com")
        self.assertEqual(row["status"], "active")
        self.assertIsNone(row["unsubscribed_at"])
        self.assertGreaterEqual(len(row["unsubscribe_token"]), 20)
        self.assertEqual(len(list_active_subscribers()), 1)

    def test_subscribe_reports_only_new_activations(self):
        created = subscribe_email("reader@example.com")
        token = self._row()["unsubscribe_token"]
        self.assertEqual(
            created,
            {"email": "reader@example.com", "unsubscribe_token": token},
        )
        self.assertIsNone(subscribe_email("reader@example.com"))
        unsubscribe_with_token(token)
        self.assertEqual(
            subscribe_email("reader@example.com"),
            {"email": "reader@example.com", "unsubscribe_token": token},
        )

    def test_duplicate_subscribe_stays_successful_and_stable(self):
        subscribe_email("reader@example.com")
        original = self._row()
        subscribe_email("READER@example.com")
        again = self._row()
        self.assertEqual(again["unsubscribe_token"], original["unsubscribe_token"])
        self.assertEqual(again["subscribed_at"], original["subscribed_at"])
        self.assertEqual(again["status"], "active")
        with db.get_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM subscribers"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_unsubscribe_and_resubscribe(self):
        subscribe_email("reader@example.com")
        token = self._row()["unsubscribe_token"]
        self.assertTrue(unsubscribe_with_token(token))
        self.assertEqual(self._row()["status"], "unsubscribed")
        self.assertIsNotNone(self._row()["unsubscribed_at"])
        self.assertEqual(list_active_subscribers(), [])
        self.assertTrue(unsubscribe_with_token(token))
        subscribe_email("reader@example.com")
        restored = self._row()
        self.assertEqual(restored["status"], "active")
        self.assertIsNone(restored["unsubscribed_at"])
        self.assertEqual(restored["unsubscribe_token"], token)
        self.assertEqual(len(list_active_subscribers()), 1)

    def test_invalid_token_changes_nothing(self):
        subscribe_email("reader@example.com")
        token = self._row()["unsubscribe_token"]
        self.assertFalse(unsubscribe_with_token("not-a-valid-token"))
        self.assertFalse(unsubscribe_with_token(token + "x"))
        self.assertFalse(token_is_known("../" + token))
        self.assertEqual(self._row()["status"], "active")
        self.assertIsNone(self._row()["unsubscribed_at"])

    def test_invalid_email_is_rejected(self):
        for value in ("", "   ", "reader", "reader@", "a@b", "a b@example.com", None, 12):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_email(value)
                with self.assertRaises(ValueError):
                    subscribe_email(value)
        with db.get_connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM subscribers"
            ).fetchone()[0]
        self.assertEqual(count, 0)
