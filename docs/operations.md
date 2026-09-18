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

This collects RSS/Atom metadata and stores new URLs in SQLite.

It does not classify articles and does not call Ollama.

Running the collector multiple times should not duplicate existing URLs.

## Run Daily Classification

```bash
python -m app.process_daily
```

This classifies every eligible unprocessed article in the configured processing window.

There is no 20-article batch limit. All eligible articles are processed sequentially.

The window is based on `published_at`, with `discovered_at` as a fallback.

Default window:

```text
processing_lookback_hours: 28
```

28 hours is a 24-hour newspaper window plus the 4-hour collection cadence. That way an article published after the last collection before processing is still classified the next morning, instead of being dropped as too old.

Historical feed entries outside that window are left unprocessed. They remain in SQLite, but they do not consume GPU time.

## Preview the Digest

```bash
python -m app.digest
```

This selects recent classified articles, applies the relevance threshold, and prints the digest. It does not send Telegram messages and does not mark articles as delivered.

## Send the Digest

```bash
python -m app.send_digest
```

This sends the digest through Telegram and marks the selected articles as delivered only after a successful send.

## Run with Docker

Build:

```bash
docker compose build
```

Collect feeds:

```bash
docker compose run --rm digest
```

Classify the daily window:

```bash
docker compose run --rm digest python -m app.process_daily
```

Preview or send the digest:

```bash
docker compose run --rm digest python -m app.digest
docker compose run --rm digest python -m app.send_digest
```

## Tests

From the project root:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

These cover daily-window selection, historical-article exclusion, the removal of the 20-item batch/round-robin processor, truncation, and processing failure/retry behavior.

They do not call Ollama.

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

Daily classification logs:

```text
logs/processor.log
```

Digest logs:

```text
logs/digest.log
```

Inspect recent entries:

```bash
tail -n 50 logs/collector.log
tail -n 50 logs/processor.log
tail -n 50 logs/digest.log
```

Follow logs:

```bash
tail -f logs/processor.log
```

Collection logs report how many new entries each source contributed.

Daily processing logs report the lookback window, how many articles were eligible, and how many succeeded or failed.

Logs should not contain full article content.

## Cron

View configured jobs:

```bash
crontab -l
```

Recommended schedule:

```cron
# Collect RSS metadata every 4 hours.
0 */4 * * * cd /home/joaoac/tech-digest && /usr/bin/docker compose run --rm digest >> logs/collector.log 2>&1

# Classify every eligible article from the daily window at 06:00.
0 6 * * * cd /home/joaoac/tech-digest && /usr/bin/docker compose run --rm digest python -m app.process_daily >> logs/processor.log 2>&1

# Send the digest at 07:00.
0 7 * * * cd /home/joaoac/tech-digest && /usr/bin/docker compose run --rm digest python -m app.send_digest >> logs/digest.log 2>&1
```

Do not point the collector cron job at the daily processor. Collection should stay cheap and independent of Ollama.

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
            processed_at = NULL,
            processing_attempts = 0,
            last_processing_error = NULL,
            failed_at = NULL
        WHERE processed_at IS NOT NULL
    ''')
"
```

Then run:

```bash
python -m app.process_daily
```

Only articles still inside the daily processing window will be classified again.

This command exists primarily for development and classifier calibration.

## Content Extraction Failures

Some pages cannot be extracted successfully.

When this happens, the processor tries a sufficiently long RSS excerpt.

If neither full content nor a usable excerpt is available, the article records a processing attempt and remains eligible for retry.

After the maximum number of attempts, the article is marked with `failed_at` and is no longer selected.

In-flight retries remain eligible even if they have aged slightly outside the daily window. That preserves retry behavior with a once-per-day processor.

## Persistent vs Temporary Data

Persistent:

```text
title
URL
source
publication date
RSS excerpt
relevance score
topics
why interesting
processing timestamps
delivery timestamp
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
source
content character count
score
features
errors
```

It should not contain the full extracted article text.
