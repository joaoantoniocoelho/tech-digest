# Authorial notes

The daily digest is curation. Occasionally an edition can open with
"a note from joão": a short intro, written by hand, that links to a new post on
the blog. The digest and the note are always in English, and the link always
points to the English post (`https://www.joaoac.com/blog/<slug>`).

Nothing is automatic. A post only reaches subscribers after a note for it is
saved and approved. The intro is the author's own text: whoever runs the
commands (the author or an agent on the server) saves the text it was given and
does not write, summarize or translate it.

## Where it runs

`python -m app.notes` writes straight to the digest's SQLite database, so it
runs on the server, next to the app. There is no admin endpoint and no token.
In production, from `~/apps/tech-digest` on the VPS (see
[production.md](production.md)):

```bash
scripts/prod app.notes save - < note.yaml
scripts/prod app.notes approve my-post
scripts/prod app.notes list
```

The commands below show the bare module; in production put `scripts/prod` in
place of `python -m`, and pass the YAML through stdin (`save -`), because files
on the VPS are not visible inside the container.

## Publishing a note

1. Publish the post on the blog.

2. Write the note as YAML. Two or three short paragraphs, separated by a blank
   line:

   ```yaml
   slug: my-post
   title: "My post"
   url: https://www.joaoac.com/blog/my-post
   published_at: 2026-11-20
   intro: |
     First paragraph.

     Second paragraph.
   ```

3. Optionally look at it inside a sample edition. This saves nothing and writes
   `preview/note-<slug>.html`:

   ```bash
   python -m app.notes preview note.yaml
   ```

4. Save it as a draft (`-` reads the YAML from stdin). Run it again after every
   edit:

   ```bash
   python -m app.notes save note.yaml
   ```

5. Approve it:

   ```bash
   python -m app.notes approve my-post
   ```

   It goes out with the next edition that has articles (07:00, Brasília time).
   Changed your mind before the send? `python -m app.notes unapprove my-post`.

6. Follow it:

   ```bash
   python -m app.notes list
   ```

   ```text
   sent      edition 2026-11-24  my-post  My post
   approved  edition -           other-post  Other post
   ```

## Rules

- Statuses: `draft` → `approved` → `sent`.
- Saving an edit to an approved note takes it back to `draft`. Only the exact
  text that was approved is ever sent.
- A sent note is frozen. It cannot be edited, approved again or sent again; it
  is the record of what subscribers received and what the archive shows.
- The URL must start with `https://www.joaoac.com/blog/` (or the bare domain).
- One note per edition. With several approved, the oldest approval goes first
  and the others follow on the next days.
- A day with no curated articles has no email, so the note waits.
- The note never triggers an extra email.
- The edition's public page shows the note too (`GET /digests/<date>` returns
  it in `note`).

## Delivery guarantee and its limit

The note is marked `sent` right after the first email carrying it is accepted
by Resend, at the same moment the edition's articles are marked delivered.
From then on it is never included again. This is at-most-once:

- If every send fails, nothing is marked and the note goes with the next run.
- If the send fails for some subscribers, they do not get the note later. A
  manual rerun of the `send` job that day builds a new email from articles not
  yet delivered, without the note.
- If the process dies between the first accepted email and the database write
  (a window of milliseconds), a rerun could send the note a second time. There
  is no per-recipient delivery log in this version.
