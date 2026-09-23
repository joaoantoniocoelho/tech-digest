# Architecture

## Overview

Tech Digest is a small self-hosted pipeline for discovering and ranking technology articles.

Its main responsibility is not content generation.

It is a filtering system:

```text
many articles
     |
     v
personal relevance analysis
     |
     v
few links worth opening
```

It behaves like a daily personal newspaper.

Collection is cheap and runs throughout the day. Classification calls TypeSafe Jev once per article, so it runs once per day before the digest is sent.

## High-Level Flow

```text
Internet
   |
   | RSS / Atom every 4 hours
   v
Feed Collector
   |
   v
SQLite metadata
   |
   | daily classification of recent unprocessed articles
   v
Article Fetcher
   |
   v
Content Extractor
   |
   | temporary text
   v
TypeSafe Jev
   |
   v
Feature Vector
   |
   v
Deterministic Scoring
   |
   v
SQLite
   |
   v
Digest Builder
   |
   v
Email
```

## Components

### Feed Collector

Implemented in:

```text
app/rss.py
app/main.py
```

Responsibilities:

- fetch RSS and Atom feeds;
- normalize basic article metadata;
- persist new URLs in SQLite;
- exit without classifying anything.

The collector exists primarily so that articles are not lost when a feed only exposes a limited number of recent entries.

Sources are configured in:

```text
config/sources.yaml
```

RSS sources may set `max_entries` to a positive integer to process only the
first entries returned by the feed; sources without it process every entry.

## Database

Implemented in:

```text
app/db.py
```

SQLite is used because the workload is small and local.

The database stores article metadata and derived classification data.

Typical article fields include:

```text
id
source
title
url
published_at
discovered_at
relevance_score
why_interesting
topics
processed_at
feed_excerpt
processing_attempts
last_processing_error
failed_at
delivered_at
```

The article URL is unique and acts as the primary deduplication mechanism.

Full article text is deliberately not stored.

Eligibility for daily classification uses `published_at` when it can be parsed, and `discovered_at` only as a fallback. This prevents a first import of a new RSS source from treating older feed entries as fresh news.

## Content Fetching

Implemented in:

```text
app/content.py
```

HTTPX is responsible for HTTP requests.

Trafilatura is responsible for extracting readable article content from HTML.

```text
URL
 |
 v
HTTPX
 |
 v
HTML
 |
 v
Trafilatura
 |
 v
Readable text
```

The extracted text exists only in memory during processing.

Once classification finishes, it is discarded.

If extraction fails, a sufficiently long RSS excerpt may be used as a fallback. If both are unavailable, the first attempt is retried. A later attempt classifies from the title and URL only, with instructions to stay conservative and not infer unsupported details. That fallback text is not stored. Long articles are truncated before they are sent to the model, keeping the beginning and the end.

## Classification

Implemented in:

```text
app/classifier.py
```

The classifier uses the official `typesafe-sdk` client and one `system_one` call per article.

Each interest feature in `config/interests.yaml` becomes a typed `Score` question (levels 0–2). A separate `importance` score uses levels 0–3.

Every feature question includes the feature description, include/exclude rules, the 0/1/2 rubric, and a conservative default-to-0 instruction.

Jev returns continuous scores. Python discretizes them with explicit thresholds, not `round()`:

```text
feature strength:  < 0.75 → 0,  < 1.50 → 1,  otherwise 2
importance:        < 0.50 → 0,  < 1.50 → 1,  < 2.50 → 2,  otherwise 3
```

The model name defaults to `jev-1.13.0` and can be overridden with `TYPESAFE_MODEL`. Authentication uses `TYPESAFE_API_KEY`.

State sent to Jev contains only:

```json
{
  "title": "...",
  "content": "..."
}
```

The classifier does not produce the final relevance score.

Instead, it extracts factual feature strengths and importance. Python builds `why_interesting` from at most three **positive** features, ordered by contribution to the score, joined with ` · `. Negative features never appear in Why or topics.

Example:

```json
{
  "feature_strengths": {
    "major_ai_model_development": 2,
    "ai_agents": 1,
    "apple_ecosystem": 0
  },
  "importance": 2,
  "why_interesting": "AI models · AI agents",
  "topics": ["AI models", "AI agents"]
}
```

Feature strength values mean:

```text
0 = does not meaningfully apply (default)
1 = explicitly present, but secondary
2 = central to the article
```

Inspect a single article without writing to SQLite:

```text
python -m app.debug_classification --url "https://..."
python -m app.debug_classification --article-id 123
```

If the TypeSafe API fails, the processor retry logic records the error and may retry on a later daily run. The API key must never appear in logs.

## Relevance Scoring

Implemented in:

```text
app/scoring.py
```

The final score is calculated in Python.

This separation is intentional:

```text
Jev:
What is this article about?

Python:
How much do I care about those things?
```

This keeps personal preferences outside the model's free-form reasoning.

It also makes scoring deterministic and easier to tune.

See:

```text
docs/relevance-scoring.md
```

## Daily Processor

Implemented in:

```text
app/processor.py
app/process_daily.py
```

The daily processor classifies every eligible unprocessed article in the configured lookback window.

There is no fixed batch size and no round-robin source selection. If 7 articles are eligible, it processes 7. If 83 are eligible, it processes 83, sequentially.

```text
unprocessed article in daily window
       |
       v
fetch article
       |
       v
extract content
       |
       v
classify features
       |
       v
calculate score
       |
       v
save derived data
```

TypeSafe API calls remain sequential. The processor does not run inference in parallel.

Articles that cannot be classified increment `processing_attempts`. After the maximum number of attempts they are marked with `failed_at` so they do not block the pipeline forever. A missing article body is retried once, then classified from title and URL metadata so a permanent fetch failure does not leave the article unclassified.

In-flight retries remain eligible even if they have aged slightly outside the daily window. Historical articles that were never attempted are left unprocessed.

## Digest and Delivery

Implemented in:

```text
app/digest.py
app/send_digest.py
app/email.py
app/telegram.py
```

The digest selects recent classified articles that:

- fall inside the digest lookback window (`lookback_hours: 24`);
- meet the relevance threshold (`minimum_score: 60`);
- stay under the article cap (`maximum_articles: 8`);
- have not already been delivered.

Each item is a compact Why line of at most three positive feature labels. Scores and topic lists are hidden in the email by default (`show_score` / `show_topics` in `config/digest.yaml`).

Delivery goes through Resend (`digest.joaoac.com`). The digest is built once, then sent separately to each active subscriber with that subscriber's unsubscribe link. Articles are marked delivered when at least one send succeeds. The Telegram sender remains in the tree, and `send_digest` does not call it.

Subscribers live in the same SQLite database (`subscribers`). `POST /subscribe` activates an address, including one that had unsubscribed. `GET /unsubscribe/<token>` only reports whether the token exists; `POST` performs the unsubscribe, so mail clients that prefetch links do not remove people. The confirmation page lives on the public site.

## Docker

The production image is a long-running process: HTTP API and in-process scheduler in one container (`python -m app.server`).

Local Compose still runs one-shot jobs. The `digest` service overrides the image command to `python -m app.main`. The `api` service uses the production command.

Persistent data:

```text
local Compose: ./data:/app/data
production:    DIGEST_DB_PATH, mounted volume
```

The database path is `DIGEST_DB_PATH`. It defaults to `data/digest.db`.

Compose loads `.env` and uses normal Docker networking. It does not use `network_mode: host` and does not talk to a local Ollama instance.

Classification requires outbound HTTPS to `api.typesafe.ai`. Logs redact API keys, job tokens, email addresses, and unsubscribe tokens.

## Scheduling

Production does not use host cron or a second Railway service. A scheduler thread inside the API process runs in `America/Sao_Paulo`:

```text
collect   every 4 hours at minute 00 (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
collect   06:45
process   06:50
send      07:00
```

A missed slot still runs if the process is up within 30 minutes for collection, or within 3 hours for processing and sending. Slots run in time order, so the 06:45 collection finishes before processing starts when both are due. A SQLite lock stops a second collect, process, or send from running at the same time. A job error is logged and does not stop the API.

Collection does not call the classifier.

Classification and digest delivery stay separate jobs, so a slow classification run cannot block feed collection. It can delay the 07:00 send until the lock is free.
