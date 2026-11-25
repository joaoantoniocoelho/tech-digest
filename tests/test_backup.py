import runpy
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import backup, db
from app.subscribers import subscribe_email


class BackupTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.db_patcher = patch.object(db, "DB_PATH", self.root / "digest.db")
        self.db_patcher.start()
        db.init_db()
        subscribe_email("reader@example.com")
        db.save_article(
            {
                "source": "Feed",
                "title": "Story",
                "url": "https://example.com/story",
                "published_at": None,
                "feed_excerpt": None,
            }
        )

    def tearDown(self):
        self.db_patcher.stop()
        self.directory.cleanup()

    def run_cli(self, *argv):
        with redirect_stdout(StringIO()) as output:
            backup.main(list(argv))
        return output.getvalue()

    def test_backup_is_a_complete_copy_taken_while_the_database_is_open(self):
        with db.get_connection():
            path = backup.create_backup()

        self.assertEqual(path.parent, self.root / "backups")
        copy = sqlite3.connect(path)
        try:
            emails = [row[0] for row in copy.execute("SELECT email FROM subscribers")]
            titles = [row[0] for row in copy.execute("SELECT title FROM articles")]
        finally:
            copy.close()
        self.assertEqual(emails, ["reader@example.com"])
        self.assertEqual(titles, ["Story"])

    def test_summary_of_a_copy_matches_the_live_database(self):
        path = backup.create_backup(self.root / "copy.db")

        live = db.database_summary()
        self.assertEqual(db.database_summary(path), live)
        self.assertEqual(live["integrity"], "ok")
        self.assertEqual(live["articles"], 1)
        self.assertEqual(live["delivered_articles"], 0)
        self.assertEqual(live["active_subscribers"], 1)
        self.assertEqual(live["notes"], 0)
        self.assertIsNone(live["last_edition"])

    def test_summary_tolerates_a_database_from_before_notes(self):
        with db.get_connection() as connection:
            connection.execute("DROP TABLE author_notes")

        summary = db.database_summary()

        self.assertIsNone(summary["notes"])
        self.assertEqual(summary["articles"], 1)

    def test_checking_a_missing_copy_does_not_create_it(self):
        missing = self.root / "nope.db"

        with self.assertRaises(SystemExit):
            self.run_cli("--check", str(missing))

        self.assertFalse(missing.exists())

    def test_only_the_newest_default_backups_are_kept(self):
        directory = backup.backup_directory()
        directory.mkdir()
        for day in range(1, 6):
            (directory / f"digest-2026110{day}-070000.db").write_bytes(b"old")
        (directory / "manual-copy.db").write_bytes(b"keep")

        path = backup.create_backup(keep=3)

        names = sorted(item.name for item in directory.iterdir())
        self.assertEqual(
            names,
            sorted(
                [
                    "digest-20261104-070000.db",
                    "digest-20261105-070000.db",
                    path.name,
                    "manual-copy.db",
                ]
            ),
        )

    def test_explicit_output_is_never_pruned_or_counted(self):
        directory = backup.backup_directory()
        directory.mkdir()
        (directory / "digest-20261101-070000.db").write_bytes(b"old")

        backup.create_backup(self.root / "export.db", keep=1)

        self.assertTrue((directory / "digest-20261101-070000.db").exists())

    def test_cli_writes_the_backup_and_prints_the_summary(self):
        output = self.run_cli("--output", str(self.root / "export.db"))

        self.assertIn(f"backup: {self.root / 'export.db'}", output)
        self.assertIn("integrity: ok", output)
        self.assertIn("active_subscribers: 1", output)
        self.assertIn("last_edition: -", output)

    def test_cli_check_reads_without_writing_a_backup(self):
        output = self.run_cli("--check")

        self.assertIn("integrity: ok", output)
        self.assertFalse(backup.backup_directory().exists())

    def test_cli_rejects_keep_below_one(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--keep", "0")


if __name__ == "__main__":
    unittest.main()
