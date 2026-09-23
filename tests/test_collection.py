import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db, main
from app.rss import fetch_feed


RSS_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Example feed</title>
<item><title>First</title><link>https://example.com/first</link></item>
<item><title>Second</title><link>https://example.com/second</link></item>
<item><title>Third</title><link>https://example.com/third</link></item>
</channel></rss>"""


class RSSMaxEntriesTests(unittest.TestCase):
    @patch("app.rss._download_feed", return_value=RSS_FEED)
    def test_without_max_entries_processes_all_entries(self, download):
        articles = fetch_feed("Example", "https://example.com/rss")
        self.assertEqual([article["title"] for article in articles], ["First", "Second", "Third"])
        download.assert_called_once()

    @patch("app.rss._download_feed", return_value=RSS_FEED)
    def test_with_max_entries_processes_first_entries(self, download):
        articles = fetch_feed("Example", "https://example.com/rss", max_entries=2)
        self.assertEqual([article["title"] for article in articles], ["First", "Second"])
        download.assert_called_once()

    @patch("app.rss._download_feed")
    def test_invalid_max_entries_are_rejected_before_fetch(self, download):
        for value in (0, -1, 1.5, "2", True, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "positive integer"):
                fetch_feed("Example", "https://example.com/rss", max_entries=value)
        download.assert_not_called()


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temporary.close()
        self.db_path = Path(temporary.name)
        self.db_patcher = patch.object(db, "DB_PATH", self.db_path)
        self.db_patcher.start()
        db.init_db()

    def tearDown(self):
        self.db_patcher.stop()
        self.db_path.unlink(missing_ok=True)

    @patch("app.main.fetch_feed", return_value=[])
    def test_config_passes_optional_max_entries_to_rss(self, fetch_feed):
        config = """sources:
  - name: OpenAI
    url: https://openai.com/news/rss.xml
    max_entries: 50
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as file:
            file.write(config)
            config_path = Path(file.name)
        self.addCleanup(config_path.unlink)
        with patch.object(main, "Path", return_value=config_path):
            with redirect_stdout(StringIO()):
                main.collect_feeds()
        fetch_feed.assert_called_once_with(
            name="OpenAI", url="https://openai.com/news/rss.xml", max_entries=50
        )

    @patch("app.main.fetch_feed")
    def test_rss_sources_collect_and_deduplicate_urls(self, fetch_feed):
        config = """sources:
  - name: Hacker News
    url: https://news.ycombinator.com/rss
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as file:
            file.write(config)
            config_path = Path(file.name)
        self.addCleanup(config_path.unlink)
        fetch_feed.return_value = [{
            "source": "Hacker News",
            "title": "RSS article",
            "url": "https://example.com/rss-article",
            "published_at": "2026-09-22",
        }]
        with patch.object(main, "Path", return_value=config_path):
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(main.collect_feeds(), 1)
                self.assertEqual(main.collect_feeds(), 0)
        fetch_feed.assert_called_with(
            name="Hacker News", url="https://news.ycombinator.com/rss"
        )
        self.assertIn("Collecting: Hacker News", output.getvalue())
        self.assertIn("Found: 1 entries", output.getvalue())
        self.assertIn("New: 0", output.getvalue())
        with db.get_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
