import runpy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db
from app.subscribers import (
    Attribution,
    list_active_subscribers,
    normalize_attribution,
    normalize_email,
    subscribe_email,
    token_is_known,
    unsubscribe_with_token,
)


class TemporaryDbTestCase(unittest.TestCase):
    initialize_db = True

    def setUp(self):
        temporary = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temporary.close()
        self.db_path = Path(temporary.name)
        self.db_patcher = patch.object(db, "DB_PATH", self.db_path)
        self.db_patcher.start()
        if self.initialize_db:
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


class SubscriberStoreTestCase(TemporaryDbTestCase):
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
            {
                "email": "reader@example.com",
                "unsubscribe_token": token,
                "action": "created",
            },
        )
        self.assertIsNone(subscribe_email("reader@example.com"))
        unsubscribe_with_token(token)
        self.assertEqual(
            subscribe_email("reader@example.com"),
            {
                "email": "reader@example.com",
                "unsubscribe_token": token,
                "action": "reactivated",
            },
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


ATTRIBUTION_COLUMNS = (
    "acquisition_source",
    "acquisition_url",
    "utm_source",
    "utm_medium",
    "utm_campaign",
)


class SubscriberAttributionTestCase(TemporaryDbTestCase):
    def _attribution(self, email="reader@example.com"):
        with db.get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {", ".join(ATTRIBUTION_COLUMNS)}
                FROM subscribers
                WHERE email = ?
                """,
                (email,),
            ).fetchone()
        return dict(row)

    def test_new_subscriber_persists_attribution(self):
        attribution = normalize_attribution(
            {
                "acquisition_source": "  LinkedIn ",
                "acquisition_url": " https://joaoac.com/digest/2026-09-29?ref=linkedin ",
                "utm_source": " newsletter ",
                "utm_medium": "social",
                "utm_campaign": "Launch-Week",
            }
        )
        subscribe_email("reader@example.com", attribution)
        self.assertEqual(
            self._attribution(),
            {
                "acquisition_source": "linkedin",
                "acquisition_url": "https://joaoac.com/digest/2026-09-29?ref=linkedin",
                "utm_source": "newsletter",
                "utm_medium": "social",
                "utm_campaign": "Launch-Week",
            },
        )

    def test_subscriber_without_attribution_stores_nulls(self):
        created = subscribe_email("reader@example.com")
        self.assertEqual(created["action"], "created")
        self.assertEqual(
            self._attribution(),
            {column: None for column in ATTRIBUTION_COLUMNS},
        )
        self.assertIsNotNone(self._row()["subscribed_at"])

    def test_first_touch_attribution_survives_repeat_and_reactivation(self):
        subscribe_email(
            "reader@example.com",
            Attribution(acquisition_source="x", utm_campaign="first"),
        )
        later = Attribution(
            acquisition_source="linkedin",
            acquisition_url="https://joaoac.com/digest?ref=linkedin",
            utm_source="li",
            utm_medium="social",
            utm_campaign="second",
        )
        self.assertIsNone(subscribe_email("reader@example.com", later))
        unsubscribe_with_token(self._row()["unsubscribe_token"])
        reactivated = subscribe_email("reader@example.com", later)
        self.assertEqual(reactivated["action"], "reactivated")
        self.assertEqual(
            self._attribution(),
            {
                "acquisition_source": "x",
                "acquisition_url": None,
                "utm_source": None,
                "utm_medium": None,
                "utm_campaign": "first",
            },
        )

    def _created_at(self, email="reader@example.com"):
        with db.get_connection() as connection:
            return connection.execute(
                "SELECT created_at FROM subscribers WHERE email = ?",
                (email,),
            ).fetchone()["created_at"]

    def test_created_at_is_set_on_insert(self):
        subscribe_email("reader@example.com")
        created_at = self._created_at()
        self.assertIsNotNone(created_at)
        self.assertEqual(created_at, self._row()["subscribed_at"])

    def test_created_at_is_kept_on_repeat_and_reactivation(self):
        subscribe_email("reader@example.com")
        original = "2026-01-01T00:00:00+00:00"
        with db.get_connection() as connection:
            connection.execute(
                "UPDATE subscribers SET created_at = ?, subscribed_at = ?",
                (original, original),
            )
        subscribe_email("reader@example.com")
        self.assertEqual(self._created_at(), original)
        unsubscribe_with_token(self._row()["unsubscribe_token"])
        self.assertEqual(subscribe_email("reader@example.com")["action"], "reactivated")
        self.assertEqual(self._created_at(), original)
        self.assertNotEqual(self._row()["subscribed_at"], original)

    def test_normalize_attribution_drops_lone_surrogates(self):
        attribution = normalize_attribution(
            {"utm_source": "a\ud800b", "acquisition_url": "\udfff"}
        )
        self.assertEqual(attribution.utm_source, "ab")
        self.assertIsNone(attribution.acquisition_url)
        subscribe_email("reader@example.com", attribution)
        self.assertEqual(self._attribution()["utm_source"], "ab")

    def test_lowercasing_source_respects_max_length(self):
        attribution = normalize_attribution({"acquisition_source": "İ" * 64})
        self.assertLessEqual(len(attribution.acquisition_source), 64)

    def test_normalize_attribution_ignores_invalid_values(self):
        attribution = normalize_attribution(
            {
                "acquisition_source": 12,
                "acquisition_url": ["https://example.com"],
                "utm_source": {"a": 1},
                "utm_medium": "   ",
                "utm_campaign": None,
            }
        )
        self.assertEqual(attribution, Attribution())
        self.assertEqual(normalize_attribution({}), Attribution())
        self.assertEqual(
            normalize_attribution({"acquisition_source": True}),
            Attribution(),
        )

    def test_normalize_attribution_truncates_and_strips_control_characters(self):
        attribution = normalize_attribution(
            {
                "acquisition_source": "X" * 500,
                "acquisition_url": "https://example.com/" + "a" * 5000,
                "utm_source": "s" * 500,
                "utm_medium": "so\x00ci\nal",
                "utm_campaign": "\t\r\n",
            }
        )
        self.assertEqual(attribution.acquisition_source, "x" * 64)
        self.assertEqual(len(attribution.acquisition_url), 2048)
        self.assertTrue(attribution.acquisition_url.startswith("https://example.com/"))
        self.assertEqual(attribution.utm_source, "s" * 128)
        self.assertEqual(attribution.utm_medium, "social")
        self.assertIsNone(attribution.utm_campaign)


class SubscriberMigrationTestCase(TemporaryDbTestCase):
    initialize_db = False

    def test_init_db_adds_attribution_columns_to_existing_table(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            CREATE TABLE subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                subscribed_at TEXT NOT NULL,
                unsubscribed_at TEXT,
                unsubscribe_token TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            """
            INSERT INTO subscribers (email, status, subscribed_at, unsubscribe_token)
            VALUES ('legacy@example.com', 'active', '2026-09-01T00:00:00+00:00',
                    'legacy-token-abcdefghijklmnop')
            """
        )
        connection.commit()
        connection.close()

        db.init_db()
        with db.get_connection() as connection:
            connection.execute(
                "UPDATE subscribers SET subscribed_at = '2026-12-01T00:00:00+00:00'"
            )
        # A second run must not re-backfill rows that already have created_at.
        db.init_db()

        with db.get_connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(subscribers)")
            }
            legacy = connection.execute(
                "SELECT * FROM subscribers WHERE email = 'legacy@example.com'"
            ).fetchone()

        self.assertTrue({"created_at", *ATTRIBUTION_COLUMNS} <= columns)
        self.assertEqual(legacy["status"], "active")
        self.assertEqual(legacy["subscribed_at"], "2026-12-01T00:00:00+00:00")
        self.assertEqual(legacy["created_at"], "2026-09-01T00:00:00+00:00")
        for column in ATTRIBUTION_COLUMNS:
            self.assertIsNone(legacy[column])

        subscribe_email(
            "new@example.com",
            Attribution(acquisition_source="shipclub"),
        )
        with db.get_connection() as connection:
            source = connection.execute(
                "SELECT acquisition_source FROM subscribers WHERE email = 'new@example.com'"
            ).fetchone()[0]
        self.assertEqual(source, "shipclub")
