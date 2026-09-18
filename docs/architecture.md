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

Collection is cheap and runs throughout the day. Classification is expensive and runs once per day, before the digest is sent.

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
Local LLM
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
Telegram
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

If extraction fails, a sufficiently long RSS excerpt may be used as a fallback. Long articles are truncated before they are sent to the model, keeping the beginning and the end.

## Classification

Implemented in:

```text
app/classifier.py
```

The classifier talks to a local Ollama instance.

The current model is:

```text
qwen3.5:9b
```

The classifier does not produce the final relevance score.

Instead, it extracts factual features from the article.

Example:

```json
{
  "feature_strengths": {
    "major_ai_model_development": 2,
    "ai_agents": 1,
    "apple_ecosystem": 0
  },
  "importance": 2,
  "why_interesting": "A major model release focused on structured automation.",
  "topics": [
    "AI Models",
    "Agents"
  ]
}
```

Feature strength values mean:

```text
0 = does not meaningfully apply
1 = related or secondary
2 = directly relevant
```

Structured output is enforced through a JSON Schema sent to Ollama.

If the model returns malformed or invalid structured output, classification is retried once before the attempt is treated as a processing failure.

## Relevance Scoring

Implemented in:

```text
app/scoring.py
```

The final score is calculated in Python.

This separation is intentional:

```text
LLM:
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

Qwen calls remain sequential. The processor does not run inference in parallel.

Articles that cannot be extracted or classified increment `processing_attempts`. After the maximum number of attempts they are marked with `failed_at` so they do not block the pipeline forever.

In-flight retries remain eligible even if they have aged slightly outside the daily window. Historical articles that were never attempted are left unprocessed.

## Digest and Delivery

Implemented in:

```text
app/digest.py
app/send_digest.py
app/telegram.py
```

The digest selects recent classified articles that:

- fall inside the digest lookback window;
- meet the relevance threshold;
- have not already been delivered.

Articles are marked delivered only after a successful Telegram send.

## Docker

The application is packaged using Docker.

The application container is intentionally short-lived.

It performs a task and exits instead of remaining online as a long-running service.

Persistent data stays on the host:

```text
./data:/app/data
```

The Ollama service runs separately on the home server.

## Scheduling

Scheduling stays on the host cron service. The application itself is not a daemon.

Recommended cadence:

```cron
# Collect RSS metadata every 4 hours.
0 */4 * * * cd /home/joaoac/tech-digest && /usr/bin/docker compose run --rm digest >> logs/collector.log 2>&1

# Classify every eligible article from the daily window at 06:00.
0 6 * * * cd /home/joaoac/tech-digest && /usr/bin/docker compose run --rm digest python -m app.process_daily >> logs/processor.log 2>&1

# Send the digest at 07:00.
0 7 * * * cd /home/joaoac/tech-digest && /usr/bin/docker compose run --rm digest python -m app.send_digest >> logs/digest.log 2>&1
```

Collection does not wake Ollama.

Classification and digest delivery are separate jobs so a slow classification run cannot block feed collection.
