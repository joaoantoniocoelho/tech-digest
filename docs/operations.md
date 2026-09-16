# Operations

## Local Development

Activate the Python environment:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Run Feed Collection

```bash
python -m app.main
```

Running the collector multiple times should not duplicate existing URLs.

## Run Article Processing

```bash
python -c "from app.processor import process_articles; process_articles()"
```

The processor currently handles up to 10 unprocessed articles per run.

## Run with Docker

Build:

```bash
docker compose build
```

Execute:

```bash
docker compose run --rm digest
```

## Database

SQLite database:

```text
data/digest.db
```

The database is persisted on the host.

It is not included in Git.

## Logs

Collector logs:

```text
logs/collector.log
```

Inspect recent entries:

```bash
tail -n 50 logs/collector.log
```

Follow logs:

```bash
tail -f logs/collector.log
```

## Cron

View configured jobs:

```bash
crontab -l
```

The current collector schedule runs once per hour.

Typical configuration:

```cron
0 * * * * cd /path/to/tech-digest && /usr/bin/docker compose run --rm digest >> logs/collector.log 2>&1
```

## Ollama

Ollama runs on the home server separately from the application.

Check available models:

```bash
curl http://localhost:11434/api/tags
```

The current classifier model is:

```text
qwen3.5:9b
```

## Reprocessing Articles

During development, classifications can be reset with:

```bash
python -c "
from app.db import get_connection

with get_connection() as connection:
    connection.execute('''
        UPDATE articles
        SET relevance_score = NULL,
            why_interesting = NULL,
            topics = NULL,
            processed_at = NULL
        WHERE processed_at IS NOT NULL
    ''')
"
```

Then run:

```bash
python -c "from app.processor import process_articles; process_articles()"
```

This command exists primarily for development and classifier calibration.

## Known Failure Mode: Content Extraction

Some pages cannot currently be extracted successfully.

When this happens:

```text
Could not extract content
```

The article remains unprocessed.

A future version should:

- use RSS content or summary as a fallback;
- track extraction attempts;
- eventually mark permanently failing articles so they do not retry forever.

## Persistent vs Temporary Data

Persistent:

```text
title
URL
source
publication date
relevance score
topics
why interesting
processing timestamps
```

Temporary:

```text
downloaded HTML
full extracted article text
LLM prompt content
```

Full article content should not be persisted or logged.

## Debugging Principle

Logging should contain operational metadata such as:

```text
article title
content character count
score
features
errors
```

It should not contain the full extracted article text.
