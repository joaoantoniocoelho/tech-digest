# Roadmap

## Current Status

The core ingestion and ranking pipeline is functional.

Implemented:

- RSS/Atom discovery
- SQLite persistence
- deduplication
- Docker execution
- cron scheduling
- HTTP article fetching
- content extraction
- local Ollama classification
- structured feature extraction
- deterministic relevance scoring

## Next: Automatic Processing

Currently:

```text
cron
  |
  v
collect feeds
```

Target:

```text
cron
  |
  v
collect feeds
  |
  v
process new articles
  |
  v
save relevance results
```

The application container should continue to behave as a short-lived job.

## Extraction Failure Handling

Add retry tracking such as:

```text
processing_attempts
last_processing_error
failed_at
```

Possible fallback:

```text
article page
     |
     | extraction failed
     v
RSS summary/content
     |
     v
classifier
```

Articles should not remain in an infinite retry loop.

## Digest Builder

Create a component that selects articles over a time window.

Initial idea:

```text
articles from last 24 hours
        |
        v
minimum relevance threshold
        |
        v
sort by score
        |
        v
top articles
```

The system should not force a fixed number of recommendations.

If only three articles are genuinely good, the digest should contain three.

Example output:

```text
Tech Digest

1. Article title

Why it's interesting:
...

Source: ...
Read: https://...
```

## Telegram Delivery

Initial Telegram integration should be intentionally simple.

Version 1:

```text
generate digest
      |
      v
Telegram Bot API
      |
      v
private message
```

No commands, buttons, or interactive features are required initially.

Possible future features:

- `/digest`
- `/latest`
- topic filters
- positive/negative feedback
- read-later actions

## Email Delivery

Email can later use the same ranked article data.

```text
ranked digest data
       |
       +---- Telegram renderer
       |
       +---- HTML email renderer
```

The classification pipeline should remain independent of presentation.

## Public Newsletter

A possible future direction is turning the personal digest into a public newsletter.

The public version should still avoid reproducing original article content.

The editorial unit remains:

```text
title
why it matters / why it is interesting
source attribution
link to original
```

The system is therefore designed as a curator rather than a republisher.

## Future Personalization

Potential improvements:

- feedback from article opens;
- explicit thumbs-up/down;
- changing feature weights over time;
- topic diversity;
- source quality signals;
- duplicate-story detection across multiple publications.

These should only be added after the basic digest proves useful in daily use.

## Non-Goals

For now, Tech Digest does not need:

- Kubernetes;
- Redis;
- PostgreSQL;
- distributed queues;
- vector databases;
- a web frontend;
- external commercial LLM APIs.

The project should remain simple until actual usage creates a reason for additional infrastructure.
