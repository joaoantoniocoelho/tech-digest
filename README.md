# Tech Digest

A self-hosted personal technology news filter with cloud-assisted classification.

Tech Digest collects articles from RSS and Atom feeds, sends article text to [TypeSafe Jev](https://typesafe.ai) for structured classification, and assigns a personalized relevance score based on my interests.

The goal is not to summarize the internet.

The goal is to answer a much simpler question:

> Which links are actually worth opening?

## Goals

Tech Digest is designed to:

- collect technology articles automatically during the day;
- classify every recent article once per day;
- avoid showing the same article twice;
- extract article content only temporarily;
- classify articles with TypeSafe Jev (typed Score decisions);
- rank articles based on a personal interest profile;
- deliver a small daily digest by email.

The final digest is intentionally lightweight.

Each recommended article should contain roughly:

```text
Article title

Why: AI agents · Developer tools · AI research

Source
Link
```

The original article remains the destination.

## Principles

### Self-hosted pipeline, cloud classifier

Collection, storage, scoring, and delivery run in one service. Locally that can be Docker Compose. In production it is a single Railway service with SQLite on a persistent volume.

Article text is sent to the TypeSafe API for classification. Titles and short RSS excerpts are sent for digest duplicate checks. Full article content is not persisted in the local SQLite database. This project does not make claims about TypeSafe's own retention.

Email delivery uses the Resend API and the verified domain `digest.joaoac.com`.

### Do not republish articles

Full article text is used temporarily for classification and is not stored in the database.

The persisted output contains metadata and derived information such as:

- relevance score;
- topics;
- why the article may be interesting (compact feature labels).

### Filter, do not summarize

The system is primarily a reading filter.

It should help reduce information overload instead of creating another large body of generated text.

### Explainable ranking

The classifier does not directly choose the final relevance score.

Instead:

1. Jev assigns a feature strength and importance score for the article;
2. deterministic Python code converts those features into a relevance score.

This makes ranking easier to inspect and tune.

## Current Architecture

```text
RSS / Atom
    |
    |  every 4 hours
    v
Feed Collector
    |
    v
SQLite metadata
    |
    |  once per day
    v
Article URL
    |
    v
HTTPX
    |
    v
Trafilatura
    |
    v
Temporary article text
    |
    v
TypeSafe Jev (system_one)
    |
    v
Feature Vector
    |
    v
Python Scoring
    |
    v
SQLite
    |
    |  once per day
    v
Ranked digest
    |
    v
Email
```

Collection and classification are separate jobs.

The collector only stores article metadata. It does not call the classifier.

Once per day, the processor classifies every unprocessed article in the recent publication window, sequentially.

The extracted article text exists only during processing and is discarded afterward.

## Configuration

Set these in `.env` (see `.env.example` and [Operations](docs/operations.md)):

- `RESEND_API_KEY` for email delivery;
- optional `RESEND_FROM` (default `Tech Digest <digest@digest.joaoac.com>`);
- `TYPESAFE_API_KEY` (required for classification and digest duplicate checks);
- optional `TYPESAFE_MODEL` (default `jev-1.13.0`);
- `PUBLIC_BASE_URL` for unsubscribe links;
- optional `DIGEST_DB_PATH` (default `data/digest.db`);
- optional `DIGEST_JOB_TOKEN` to enable `POST /jobs/collect`, `/jobs/process`, and `/jobs/send`.

Digest recipients are rows in `subscribers`, not `RESEND_TO`.

Docker Compose loads `.env` automatically. A local Python shell does not, so export the same variables before `process_daily`, `send_digest`, or `debug_classification`.

## Current Features

- RSS and Atom feed collection
- YAML-based source configuration
- SQLite persistence
- URL-based deduplication
- Dockerized collector and daily jobs
- Scheduled collection through cron
- Daily classification of all recent articles
- Article extraction using HTTPX and Trafilatura
- TypeSafe Jev classification (typed Score outputs)
- Debug classification without writing to SQLite
- Deterministic relevance scoring
- Personalized interest profile
- Daily ranked digest
- Jev comparison of titles and RSS excerpts to remove repeated stories across sources before filling the digest
- Email delivery through Resend
- Processing logs

## Project Structure

```text
tech-digest/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── process_daily.py
│   ├── rss.py
│   ├── db.py
│   ├── content.py
│   ├── classifier.py
│   ├── debug_classification.py
│   ├── processor.py
│   ├── scoring.py
│   ├── digest.py
│   ├── send_digest.py
│   ├── email.py
│   └── telegram.py
│
├── config/
│   ├── sources.yaml
│   ├── interests.yaml
│   └── digest.yaml
│
├── data/
│   └── digest.db
│
├── tests/
│
├── logs/
│
├── docs/
│   ├── architecture.md
│   ├── relevance-scoring.md
│   ├── operations.md
│   └── roadmap.md
│
├── Dockerfile
├── compose.yaml
├── railway.toml
├── requirements.txt
└── README.md
```

## Daily Lifecycle

```text
periodic lightweight collection
        ↓
SQLite metadata
        ↓
daily classification of all recent articles
        ↓
daily ranked digest
        ↓
Email
```

The daily processor selects articles by publication time (`published_at`), falling back to `discovered_at` only when no usable publication timestamp exists.

That keeps a first import of an RSS source from treating old feed entries as today's news.

## Running Locally

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Classification talks to the TypeSafe API (`typesafe-sdk`). Export `TYPESAFE_API_KEY` first, for example:

```bash
set -a && source .env && set +a
```

Collect feeds without classifying anything:

```bash
python -m app.main
```

Classify every eligible article from the daily window:

```bash
python -m app.process_daily
```

Preview the digest:

```bash
python -m app.digest
```

Debug one article without saving classification:

```bash
python -m app.debug_classification --url "https://example.com/article"
python -m app.debug_classification --article-id 123
```

Send the digest by email:

```bash
python -m app.send_digest
```

## Local end-to-end test

Docker is enough. You do not need the virtualenv or a host Python. Compose starts the API and stores SQLite at `data/dev.db` on your machine. There is no separate database container. `data/digest.db` is left untouched.

`.env` still has to contain `TYPESAFE_API_KEY` and `RESEND_API_KEY`. Compose loads that file, then forces the dev database and `PUBLIC_BASE_URL=http://127.0.0.1:8080`.

Start the API:

```bash
docker compose up --build api
```

In another terminal, register the address that should receive the test:

```bash
curl -s -X POST http://127.0.0.1:8080/subscribe \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com"}'
```

Run the pipeline inside that same container:

```bash
docker compose exec api python -m app.main
docker compose exec api python -m app.process_daily
docker compose exec api python -m app.digest
docker compose exec api python -m app.send_digest
```

Classification calls the TypeSafe API. `app.digest` only prints the edition. `app.send_digest` sends a real email when there is an active subscriber and at least one article from the last 24 hours with a score of at least 60 that has not been delivered yet.

Stop the API before deleting the dev database, then start it again:

```bash
docker compose stop api
rm -f data/dev.db data/dev.db-wal data/dev.db-shm
docker compose up api
```

The next start creates an empty database. Subscribe again after that. `data/digest.db` stays as it was.

The same dev database is used by one-shot commands:

```bash
docker compose run --rm digest
docker compose run --rm digest python -m app.process_daily
docker compose run --rm digest python -m app.digest
docker compose run --rm digest python -m app.send_digest
```

### Without Docker

Activate the virtualenv. A bare `python3` on macOS is often Python 3.9 and does not have the project dependencies.

```bash
source .venv/bin/activate
set -a && source .env && set +a
export DIGEST_DB_PATH=data/dev.db
export PUBLIC_BASE_URL=http://127.0.0.1:8080
```

```bash
python -c "
from app.db import init_db
from app.subscribers import subscribe_email
init_db()
subscribe_email('you@example.com')
"
python -m app.main
python -m app.process_daily
python -m app.digest
python -m app.send_digest
```

Clear that same file with `rm -f data/dev.db data/dev.db-wal data/dev.db-shm`.

## Documentation

More details:

- [Architecture](docs/architecture.md)
- [Relevance Scoring](docs/relevance-scoring.md)
- [Operations](docs/operations.md)
- [Roadmap](docs/roadmap.md)

## Status

Tech Digest is currently under active development.

The daily newspaper pipeline is functional: periodic collection, daily classification, ranked digest, and email delivery.
