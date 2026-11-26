import runpy
import unittest
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

import yaml

from app.filters import is_blocked_title, is_launch_post

FILTERS = {
    "blocked_phrases": ["is hiring"],
    "blocked_words": ["fuck", "shit", "bullshit", "cunt"],
    "block_dated_titles": True,
}


class BlockedTitleTestCase(unittest.TestCase):
    def _blocked(self, title, filters=None):
        return is_blocked_title(title, FILTERS if filters is None else filters, current_year=2026)

    def test_job_posts_are_blocked(self):
        self.assertTrue(self._blocked("Tasklet (YC P26) Is Hiring a Founding Engineer"))
        self.assertTrue(self._blocked("Acme  is   hiring"))

    def test_articles_about_hiring_are_kept(self):
        self.assertFalse(self._blocked("Hiring is broken"))
        self.assertFalse(self._blocked("This hiring process works"))

    def test_profanity_is_blocked_with_inflections(self):
        self.assertTrue(self._blocked("Fuck Your AI Safeguards"))
        self.assertTrue(self._blocked("A fucking fast parser"))
        self.assertTrue(self._blocked("The bullshit benchmark"))

    def test_innocent_words_are_kept(self):
        self.assertFalse(self._blocked("Scunthorpe problem revisited"))
        self.assertFalse(self._blocked("Mishits in table tennis tracking"))

    def test_past_year_tags_are_blocked(self):
        self.assertTrue(self._blocked("Switcher (2004)"))
        self.assertTrue(self._blocked("Worse is better (1991) "))

    def test_current_year_and_other_numbers_are_kept(self):
        self.assertFalse(self._blocked("State of databases (2026)"))
        self.assertFalse(self._blocked("Tasklet (YC P26) raises a round"))
        self.assertFalse(self._blocked("The year 2004 in review"))
        self.assertFalse(self._blocked("(2004) was a good year for Gmail"))

    def test_dated_rule_is_opt_in(self):
        self.assertFalse(self._blocked("Switcher (2004)", {"blocked_phrases": ["is hiring"]}))

    def test_no_filters_blocks_nothing(self):
        self.assertFalse(self._blocked("Acme is hiring", {}))


class LaunchPostTestCase(unittest.TestCase):
    def test_show_and_launch_prefixes(self):
        self.assertTrue(is_launch_post("Show HN: A tiny database"))
        self.assertTrue(is_launch_post("  launch hn: Tasklet"))

    def test_other_titles(self):
        self.assertFalse(is_launch_post("Ask HN: What are you working on?"))
        self.assertFalse(is_launch_post("How to Show HN properly"))


class ShippedConfigTestCase(unittest.TestCase):
    def test_config_blocks_the_known_bad_titles(self):
        with Path("config/digest.yaml").open() as file:
            filters = yaml.safe_load(file)["title_filters"]

        for title in (
            "Tasklet (YC P26) Is Hiring a Founding Engineer",
            "Fuck Your AI Safeguards",
            "Switcher (2004)",
        ):
            with self.subTest(title=title):
                self.assertTrue(is_blocked_title(title, filters, current_year=2026))

        self.assertFalse(
            is_blocked_title("We promoted developers into architects", filters, current_year=2026)
        )


if __name__ == "__main__":
    unittest.main()
