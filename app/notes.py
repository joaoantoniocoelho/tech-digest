"""Authorial notes: validation and the editorial CLI.

The CLI writes straight to the digest database, so it runs where the database
is (on the server, by the author or an agent) and never needs a deploy.
"""

import argparse
import re
import sys
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

import yaml

from app.db import (
    init_db,
    list_notes,
    save_note_draft,
    set_note_approval,
)

# Notes always link to the English blog; the digest is English only.
BLOG_URL_PREFIXES = ("https://www.joaoac.com/blog/", "https://joaoac.com/blog/")
PREVIEW_DIR = Path("preview")

MAX_SLUG_LENGTH = 120
MAX_TITLE_LENGTH = 200
MAX_URL_LENGTH = 300
MAX_INTRO_LENGTH = 3000

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def note_paragraphs(intro: str) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", intro.replace("\r\n", "\n").replace("\r", "\n"))
    return [" ".join(paragraph.split()) for paragraph in paragraphs if paragraph.strip()]


def _required_text(payload: Mapping[str, object], field: str, limit: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} is required")

    text = " ".join(value.split())
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > limit:
        raise ValueError(f"{field} is longer than {limit} characters")
    return text


def _published_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    # YAML turns a bare 2026-11-20 into a date object.
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError("published_at must be a YYYY-MM-DD date")

    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as error:
        raise ValueError("published_at must be a YYYY-MM-DD date") from error


def normalize_note(payload: Mapping[str, object]) -> dict:
    slug = payload.get("slug")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug) or len(slug) > MAX_SLUG_LENGTH:
        raise ValueError("slug must be lowercase letters, digits and hyphens")

    url = payload.get("url")
    if (
        not isinstance(url, str)
        or len(url) > MAX_URL_LENGTH
        or any(character.isspace() for character in url)
        or not any(
            url.startswith(prefix) and len(url) > len(prefix) for prefix in BLOG_URL_PREFIXES
        )
    ):
        raise ValueError(f"url must start with {BLOG_URL_PREFIXES[0]}")

    intro = payload.get("intro")
    if not isinstance(intro, str):
        raise ValueError("intro is required")

    paragraphs = note_paragraphs(intro)
    if not paragraphs:
        raise ValueError("intro is required")

    normalized_intro = "\n\n".join(paragraphs)
    if len(normalized_intro) > MAX_INTRO_LENGTH:
        raise ValueError(f"intro is longer than {MAX_INTRO_LENGTH} characters")

    return {
        "slug": slug,
        "title": _required_text(payload, "title", MAX_TITLE_LENGTH),
        "url": url,
        "intro": normalized_intro,
        "published_at": _published_date(payload.get("published_at")),
    }


def load_note_file(source: str) -> dict:
    """Read a note from a YAML file, or from stdin when the source is "-"."""
    try:
        if source == "-":
            payload = yaml.safe_load(sys.stdin.read())
        else:
            path = Path(source)
            if not path.is_file():
                raise ValueError(f"{path} does not exist")
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"note is not valid YAML: {error}") from error

    if not isinstance(payload, dict):
        raise ValueError("note must be a YAML mapping")

    return normalize_note(payload)


def _command_save(args: argparse.Namespace) -> None:
    note = load_note_file(args.file)
    action = save_note_draft(note)
    if action == "sent":
        raise ValueError(f"note {note['slug']} was already sent and cannot be changed")

    print(f"Note {note['slug']} {action} as draft. Approve it when it is ready.")


def _command_preview(args: argparse.Namespace) -> None:
    from app.digest import render_text_digest, sample_digest
    from app.email import render_html_digest

    note = load_note_file(args.file)
    digest = {**sample_digest(), "note": note}
    sent_at = datetime.now().astimezone()

    print()
    print(render_text_digest(digest, show_score=False, show_topics=False, sent_at=sent_at))
    print()

    path = PREVIEW_DIR / f"note-{note['slug']}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_html_digest(
            digest=digest,
            sent_at=sent_at,
            unsubscribe_url="https://digest.joaoac.com/unsubscribe/preview",
        ),
        encoding="utf-8",
    )
    print(f"HTML preview written to {path}")


def _set_approval(slug: str, approved: bool) -> None:
    outcome = set_note_approval(slug, approved=approved)
    if outcome == "missing":
        raise ValueError(f"note {slug} does not exist")
    if outcome == "sent":
        raise ValueError(f"note {slug} was already sent and cannot be changed")


def _command_approve(args: argparse.Namespace) -> None:
    _set_approval(args.slug, approved=True)
    print(f"Note {args.slug} approved. It goes out with the next edition that has articles.")


def _command_unapprove(args: argparse.Namespace) -> None:
    _set_approval(args.slug, approved=False)
    print(f"Note {args.slug} is a draft again.")


def _command_list(_args: argparse.Namespace) -> None:
    notes = list_notes()
    if not notes:
        print("No notes yet.")
        return

    for note in notes:
        edition = note["edition_date"] or "-"
        print(f"{note['status']:<9} edition {edition:<10}  {note['slug']}  {note['title']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Manage the digest's authorial notes.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="Every note with its status and edition.").set_defaults(
        run=_command_list
    )

    for name, run, description in (
        ("save", _command_save, "Create or update a note as a draft."),
        ("preview", _command_preview, "Render a sample edition with the note; saves nothing."),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("file", help='YAML file with the note, or "-" for stdin.')
        command.set_defaults(run=run)

    for name, run, description in (
        ("approve", _command_approve, "Mark a saved note as ready to send."),
        ("unapprove", _command_unapprove, "Take an approved note back to draft."),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("slug")
        command.set_defaults(run=run)

    args = parser.parse_args(argv)

    try:
        init_db()
        args.run(args)
    except ValueError as error:
        raise SystemExit(f"error: {error}") from error


if __name__ == "__main__":
    main()
