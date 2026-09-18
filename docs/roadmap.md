# Roadmap

## Current Status

The daily newspaper pipeline is functional.

Implemented:

- RSS/Atom discovery
- SQLite persistence
- deduplication
- Docker execution
- periodic collection without classification
- daily sequential classification of all recent articles
- HTTP article fetching
- content extraction with RSS excerpt fallback
- local Ollama classification
- structured feature extraction
- one retry for invalid structured model output
- deterministic relevance scoring
- ranked digest over a publication-time window
- Telegram delivery after successful send

## Next: Daily Use and Calibration

The next work is operational rather than architectural:

- watch a few days of real digests;
- tune feature weights and the relevance threshold;
- add or remove sources based on actual usefulness;
- notice repeated stories across publications.

Keep the system a short-lived cron job on SQLite.

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
- Celery;
- PostgreSQL;
- distributed queues;
- vector databases;
- a web frontend;
- external commercial LLM APIs;
- parallel LLM inference.

The project should remain simple until actual usage creates a reason for additional infrastructure.
