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

## High-Level Flow

```text
Internet
   |
   | RSS / Atom
   v
Feed Collector
   |
   v
SQLite
   |
   | unprocessed article
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
Future Digest Builder
   |
   +------> Telegram
   |
   +------> Email
```

## Components

### Feed Collector

Implemented in:

```text
app/rss.py
```

Responsibilities:

- fetch RSS and Atom feeds;
- normalize basic article metadata;
- return discovered articles.

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
```

The article URL is unique and acts as the primary deduplication mechanism.

Full article text is deliberately not stored.

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

## Processor

Implemented in:

```text
app/processor.py
```

The processor connects the content extraction, classification, and scoring stages.

Current flow:

```text
unprocessed article
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

At the moment, the processor can be executed manually.

Automatic integration with the scheduled collector is the next implementation step.

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

The feed collector is currently triggered by the host's cron service.

Example:

```cron
0 * * * * cd /path/to/tech-digest && /usr/bin/docker compose run --rm digest >> logs/collector.log 2>&1
```

The design intentionally keeps scheduling outside the application.

## Future Delivery

The classification pipeline is independent of the delivery mechanism.

Future renderers can consume the same ranked article data:

```text
ranked articles
      |
      +---- Telegram renderer
      |
      +---- Email renderer
      |
      +---- Web renderer
```

This allows the personal digest to potentially evolve into a public newsletter without changing the core ingestion and ranking pipeline.
