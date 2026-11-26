import os
import runpy
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))

from app import db, notes
from app.send_digest import send_daily_digest
from app.share import edition_date

URL = "https://www.joaoac.com/blog/my-post"


def _payload(**overrides):
    payload = {
        "slug": "my-post",
        "title": "My post",
        "url": URL,
        "intro": "First paragraph.\n\nSecond paragraph.",
        "published_at": "2026-11-20",
    }
    payload.update(overrides)
    return payload


class NormalizeNoteTestCase(unittest.TestCase):
    def test_normalizes_whitespace_and_paragraphs(self):
        note = notes.normalize_note(
            _payload(
                title="  My   post ",
                intro="First line\nwrapped.\r\n\r\n\r\n  Second   paragraph. \n",
                published_at=date(2026, 11, 20),
            )
        )

        self.assertEqual(
            note,
            {
                "slug": "my-post",
                "title": "My post",
                "url": URL,
                "intro": "First line wrapped.\n\nSecond paragraph.",
                "published_at": "2026-11-20",
            },
        )

    def test_published_at_is_optional(self):
        self.assertIsNone(notes.normalize_note(_payload(published_at=None))["published_at"])
        self.assertIsNone(notes.normalize_note(_payload(published_at=""))["published_at"])

    def test_url_is_optional_for_a_standalone_comment(self):
        for missing in ("", None):
            with self.subTest(url=missing):
                self.assertEqual(notes.normalize_note(_payload(url=missing))["url"], "")

        payload = _payload()
        del payload["url"]
        self.assertEqual(notes.normalize_note(payload)["url"], "")

    def test_rejects_invalid_fields(self):
        invalid = {
            "slug": ["", "My-Post", "a b", "-a", "a" * 121, None],
            "title": ["", "   ", None, "x" * 201],
            "url": [
                123,
                "https://www.joaoac.com/blog/",
                "https://www.joaoac.com/pt-BR/blog/my-post",
                "http://www.joaoac.com/blog/my-post",
                "https://example.com/blog/my-post",
                "javascript:alert(1)",
                "https://www.joaoac.com/blog/my post",
            ],
            "intro": ["", " \n\n ", None, "x" * 3001],
            "published_at": ["yesterday", "2026-13-01", 20261120],
        }

        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    notes.normalize_note(_payload(**{field: value}))

    def test_accepts_bare_domain(self):
        url = "https://joaoac.com/blog/my-post"
        self.assertEqual(notes.normalize_note(_payload(url=url))["url"], url)


class NoteDatabaseTestCase(unittest.TestCase):
    def setUp(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temporary:
            self.db_path = Path(temporary.name)
        self.db_patcher = patch.object(db, "DB_PATH", self.db_path)
        self.db_patcher.start()
        db.init_db()

    def tearDown(self):
        self.db_patcher.stop()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)

    def save(self, slug="my-post", **overrides):
        return db.save_note_draft(
            notes.normalize_note(
                _payload(slug=slug, url=f"https://www.joaoac.com/blog/{slug}", **overrides)
            )
        )


class NoteLifecycleTestCase(NoteDatabaseTestCase):
    def test_init_db_is_idempotent_and_keeps_notes(self):
        self.save()
        db.init_db()

        self.assertEqual([note["slug"] for note in db.list_notes()], ["my-post"])

    def test_new_note_is_a_draft_and_is_not_selected(self):
        self.assertEqual(self.save(), "created")

        note = db.get_note("my-post")
        assert note is not None
        self.assertEqual(note["status"], "draft")
        self.assertIsNone(note["approved_at"])
        self.assertIsNone(db.get_pending_note("2026-11-23"))

    def test_approved_note_is_selected_and_unapprove_withdraws_it(self):
        self.save()
        self.assertEqual(db.set_note_approval("my-post", approved=True), "ok")

        pending = db.get_pending_note("2026-11-23")
        assert pending is not None
        self.assertEqual(pending["slug"], "my-post")
        self.assertEqual(pending["status"], "approved")

        self.assertEqual(db.set_note_approval("my-post", approved=False), "ok")
        self.assertIsNone(db.get_pending_note("2026-11-23"))

    def test_editing_an_approved_note_requires_new_approval(self):
        self.save()
        db.set_note_approval("my-post", approved=True)

        self.assertEqual(self.save(title="Edited"), "updated")

        note = db.get_note("my-post")
        assert note is not None
        self.assertEqual(note["title"], "Edited")
        self.assertEqual(note["status"], "draft")
        self.assertIsNone(note["approved_at"])
        self.assertIsNone(db.get_pending_note("2026-11-23"))

    def test_unknown_note_cannot_be_approved(self):
        self.assertEqual(db.set_note_approval("nope", approved=True), "missing")

    def test_oldest_approval_goes_first(self):
        self.save("second")
        self.save("first")
        with patch.object(db, "_utc_timestamp", return_value="2026-11-20T10:00:00+00:00"):
            db.set_note_approval("first", approved=True)
        with patch.object(db, "_utc_timestamp", return_value="2026-11-21T10:00:00+00:00"):
            db.set_note_approval("second", approved=True)

        pending = db.get_pending_note("2026-11-23")
        assert pending is not None
        self.assertEqual(pending["slug"], "first")

    def test_sent_note_is_recorded_once_and_frozen(self):
        self.save()
        db.set_note_approval("my-post", approved=True)
        note = db.get_pending_note("2026-11-23")
        assert note is not None

        self.assertTrue(db.mark_note_sent(note["id"], "2026-11-23"))
        self.assertFalse(db.mark_note_sent(note["id"], "2026-11-24"))

        stored = db.get_note("my-post")
        assert stored is not None
        self.assertEqual(stored["status"], "sent")
        self.assertEqual(stored["edition_date"], "2026-11-23")
        self.assertIsNotNone(stored["sent_at"])

        self.assertEqual(self.save(title="Rewritten"), "sent")
        self.assertEqual(db.set_note_approval("my-post", approved=True), "sent")
        self.assertEqual(db.set_note_approval("my-post", approved=False), "sent")
        unchanged = db.get_note("my-post")
        assert unchanged is not None
        self.assertEqual(unchanged["title"], "My post")
        self.assertEqual(unchanged["edition_date"], "2026-11-23")

    def test_draft_cannot_be_marked_sent(self):
        self.save()
        note = db.get_note("my-post")
        assert note is not None

        self.assertFalse(db.mark_note_sent(note["id"], "2026-11-23"))

    def test_edition_carries_one_note_and_next_edition_takes_the_next(self):
        for slug in ("first", "second"):
            self.save(slug)
            db.set_note_approval(slug, approved=True)
        first = db.get_pending_note("2026-11-23")
        assert first is not None
        db.mark_note_sent(first["id"], "2026-11-23")

        # A second run on the same day must not attach another note.
        self.assertIsNone(db.get_pending_note("2026-11-23"))
        following = db.get_pending_note("2026-11-24")
        assert following is not None
        self.assertNotEqual(following["slug"], first["slug"])

    def test_edition_note_exposes_only_public_fields(self):
        self.save()
        db.set_note_approval("my-post", approved=True)
        self.assertIsNone(db.get_edition_note("2026-11-23"))

        note = db.get_pending_note("2026-11-23")
        assert note is not None
        db.mark_note_sent(note["id"], "2026-11-23")

        self.assertEqual(
            db.get_edition_note("2026-11-23"),
            {
                "title": "My post",
                "url": URL,
                "intro": "First paragraph.\n\nSecond paragraph.",
            },
        )
        self.assertIsNone(db.get_edition_note("2026-11-24"))


class SendWithNoteTestCase(NoteDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.patchers = [
            patch("app.send_digest.build_digest", side_effect=self.build_digest),
            patch("app.send_digest.list_active_subscribers", return_value=self.subscribers()),
            patch("app.send_digest.send_email", return_value={"id": "email_1"}),
            patch("app.send_digest.revalidate_published_edition"),
            patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://api.digest.joaoac.com"}),
        ]
        self.build, _subscribers, self.send_email, _revalidate, _env = (
            patcher.start() for patcher in self.patchers
        )
        self.articles = [self.article()]

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        super().tearDown()

    def article(self):
        return {
            "id": 7,
            "title": "Curated story",
            "relevance_score": 80,
            "why_interesting": "AI agents",
            "source": "Test",
            "topics": [],
            "url": "https://example.com",
            "published_at": None,
        }

    def subscribers(self):
        return [
            {"id": 1, "email": "one@example.com", "unsubscribe_token": "token-one-aaaa"},
            {"id": 2, "email": "two@example.com", "unsubscribe_token": "token-two-bbbb"},
        ]

    def build_digest(self):
        return {"lookback_hours": 24, "articles": list(self.articles)}

    def approve(self, slug="my-post"):
        self.save(slug)
        db.set_note_approval(slug, approved=True)

    def run_send(self):
        with redirect_stdout(StringIO()):
            return send_daily_digest()

    def bodies(self):
        return [
            call.kwargs["html_body"] + call.kwargs["text"]
            for call in self.send_email.call_args_list
        ]

    def stored(self, slug="my-post"):
        note = db.get_note(slug)
        assert note is not None
        return note

    def test_approved_note_goes_to_every_subscriber_and_is_marked_sent(self):
        self.approve()

        self.run_send()

        self.assertEqual(self.send_email.call_count, 2)
        for call in self.send_email.call_args_list:
            html, text = call.kwargs["html_body"], call.kwargs["text"]
            self.assertIn("a note from joão", html)
            self.assertIn(f'href="{URL}"', html)
            self.assertLess(html.index("My post"), html.index("Curated story"))
            self.assertIn("a note from joão", text)
            self.assertLess(text.index("My post"), text.index("Curated story"))

        note = self.stored()
        self.assertEqual(note["status"], "sent")
        self.assertEqual(note["edition_date"], edition_date(datetime.now(timezone.utc)))

    def test_draft_is_never_sent(self):
        self.save()

        self.run_send()

        self.assertNotIn("a note from joão", "".join(self.bodies()))
        self.assertEqual(self.stored()["status"], "draft")

    def test_rerun_does_not_repeat_the_note(self):
        self.approve()
        self.run_send()
        self.send_email.reset_mock()

        self.run_send()

        self.assertEqual(self.send_email.call_count, 2)
        self.assertNotIn("a note from joão", "".join(self.bodies()))
        self.assertNotIn("My post", "".join(self.bodies()))

    def test_same_day_rerun_does_not_attach_the_next_note(self):
        self.approve("first")
        self.run_send()
        self.approve("second")
        self.send_email.reset_mock()

        self.run_send()

        self.assertNotIn("a note from joão", "".join(self.bodies()))
        self.assertEqual(self.stored("second")["status"], "approved")

    def test_next_day_takes_the_next_approved_note(self):
        self.approve("first")
        self.run_send()
        self.approve("second")
        self.send_email.reset_mock()
        tomorrow = datetime.now().astimezone() + timedelta(days=1)

        with patch("app.send_digest.datetime") as clock:
            clock.now.return_value = tomorrow
            self.run_send()

        self.assertIn("a note from joão", "".join(self.bodies()))
        self.assertEqual(self.stored("second")["edition_date"], edition_date(tomorrow))

    def test_note_stays_approved_when_every_send_fails(self):
        self.approve()
        self.send_email.side_effect = RuntimeError("Resend down")

        with self.assertRaises(RuntimeError):
            self.run_send()

        note = self.stored()
        self.assertEqual(note["status"], "approved")
        self.assertIsNone(note["edition_date"])

        self.send_email.side_effect = None
        self.send_email.reset_mock()
        self.run_send()

        self.assertIn("a note from joão", "".join(self.bodies()))
        self.assertEqual(self.stored()["status"], "sent")

    def test_partial_failure_marks_sent_after_the_first_success(self):
        self.approve()
        self.send_email.side_effect = [RuntimeError("Resend down"), {"id": "email_2"}]

        self.run_send()

        self.assertEqual(self.stored()["status"], "sent")

        # At-most-once: the retry sends curation only, even to the failed subscriber.
        self.send_email.side_effect = None
        self.send_email.reset_mock()
        self.run_send()
        self.assertNotIn("a note from joão", "".join(self.bodies()))

    def test_note_waits_when_there_are_no_articles(self):
        self.approve()
        self.articles = []

        self.run_send()

        self.send_email.assert_not_called()
        self.assertEqual(self.stored()["status"], "approved")


class NotesCliTestCase(NoteDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.preview_patcher = patch.object(notes, "PREVIEW_DIR", self.root / "preview")
        self.preview_patcher.start()

    def tearDown(self):
        self.preview_patcher.stop()
        self.directory.cleanup()
        super().tearDown()

    def run_cli(self, *argv):
        with redirect_stdout(StringIO()) as output:
            notes.main(list(argv))
        return output.getvalue()

    def note_yaml(self, **overrides):
        payload = _payload(**overrides)
        return (
            f"slug: {payload['slug']}\n"
            f"title: {payload['title']}\n"
            f"url: {payload['url']}\n"
            f"published_at: {payload['published_at']}\n"
            "intro: |\n  First paragraph.\n\n  Second paragraph.\n"
        )

    def write_note(self, **overrides):
        path = self.root / "note.yaml"
        path.write_text(self.note_yaml(**overrides), encoding="utf-8")
        return str(path)

    def stored(self):
        note = db.get_note("my-post")
        assert note is not None
        return note

    def test_save_then_approve_makes_the_note_pending(self):
        output = self.run_cli("save", self.write_note())

        self.assertIn("created as draft", output)
        self.assertEqual(self.stored()["status"], "draft")
        self.assertEqual(self.stored()["intro"], "First paragraph.\n\nSecond paragraph.")
        self.assertIsNone(db.get_pending_note("2026-11-23"))

        self.run_cli("approve", "my-post")

        pending = db.get_pending_note("2026-11-23")
        assert pending is not None
        self.assertEqual(pending["slug"], "my-post")

        self.run_cli("unapprove", "my-post")
        self.assertIsNone(db.get_pending_note("2026-11-23"))

    def test_saving_again_updates_and_drops_the_approval(self):
        self.run_cli("save", self.write_note())
        self.run_cli("approve", "my-post")

        output = self.run_cli("save", self.write_note(title="Edited"))

        self.assertIn("updated as draft", output)
        self.assertEqual(self.stored()["title"], "Edited")
        self.assertEqual(self.stored()["status"], "draft")

    def test_save_reads_the_note_from_stdin(self):
        with patch.object(notes.sys, "stdin", StringIO(self.note_yaml())):
            self.run_cli("save", "-")

        self.assertEqual(self.stored()["title"], "My post")

    def test_invalid_note_is_not_saved(self):
        path = self.write_note(url="https://www.joaoac.com/pt-BR/blog/my-post")

        with self.assertRaises(SystemExit) as error:
            self.run_cli("save", path)

        self.assertIn("url must start with", str(error.exception))
        self.assertIsNone(db.get_note("my-post"))

    def test_broken_yaml_and_missing_file_are_reported_without_a_traceback(self):
        path = self.root / "broken.yaml"
        path.write_text("slug: my-post\nintro: | text on the marker line\n", encoding="utf-8")

        with self.assertRaises(SystemExit) as error:
            self.run_cli("save", str(path))
        self.assertIn("is not valid YAML", str(error.exception))

        with self.assertRaises(SystemExit) as error:
            self.run_cli("save", str(self.root / "nope.yaml"))
        self.assertIn("does not exist", str(error.exception))

    def test_unknown_note_cannot_be_approved(self):
        with self.assertRaises(SystemExit) as error:
            self.run_cli("approve", "nope")

        self.assertIn("does not exist", str(error.exception))

    def test_sent_note_cannot_be_changed(self):
        path = self.write_note()
        self.run_cli("save", path)
        self.run_cli("approve", "my-post")
        db.mark_note_sent(self.stored()["id"], "2026-11-23")

        for argv in (("save", path), ("approve", "my-post"), ("unapprove", "my-post")):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as error:
                self.run_cli(*argv)
            self.assertIn("already sent", str(error.exception))

        self.assertEqual(self.stored()["status"], "sent")

    def test_list_shows_status_and_edition(self):
        self.assertIn("No notes yet.", self.run_cli("list"))

        self.run_cli("save", self.write_note())
        self.run_cli("approve", "my-post")
        db.mark_note_sent(self.stored()["id"], "2026-11-23")

        output = self.run_cli("list")
        self.assertIn("sent", output)
        self.assertIn("2026-11-23", output)
        self.assertIn("my-post", output)

    def test_preview_writes_html_and_saves_nothing(self):
        output = self.run_cli("preview", self.write_note())

        html = (notes.PREVIEW_DIR / "note-my-post.html").read_text(encoding="utf-8")
        self.assertIn("a note from joão", html)
        self.assertIn("First paragraph.", html)
        self.assertIn("a note from joão", output)
        self.assertIsNone(db.get_note("my-post"))


if __name__ == "__main__":
    unittest.main()
