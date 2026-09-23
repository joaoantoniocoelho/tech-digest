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

Docker Compose loads `.env`. Local Python does not. Export the same variables before classification:

```bash
set -a && source .env && set +a
```

## Run Feed Collection

```bash
python -m app.main
```

This collects RSS/Atom metadata and stores new URLs in SQLite.

It does not classify articles and does not call TypeSafe.

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

Historical feed entries outside that window are left unprocessed. They remain in SQLite, but they are not sent to the classifier.

## Preview the Digest

```bash
python -m app.digest
```

This selects recent classified articles, applies the relevance threshold (`minimum_score: 60`) and article cap (`maximum_articles: 8`), and prints the digest. It does not send email and does not mark articles as delivered.

`config/digest.yaml` hides scores and topic lists in the rendered digest by default (`show_score: false`, `show_topics: false`). The Why line is the compact feature labels.

## Send the Digest

```bash
python -m app.send_digest
```

This sends the digest by email through Resend and marks the selected articles as delivered only after a successful send.

Set in `.env`:

```text
RESEND_API_KEY=...
RESEND_TO=joaoantonioscoelho@gmail.com
```

The sender defaults to `Tech Digest <digest@digest.joaoac.com>`. Override it with `RESEND_FROM` if needed. `app/telegram.py` stays in the project, and `send_digest` does not call it.

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

Compose reads `.env` and uses normal Docker networking. Classification needs outbound HTTPS to `api.typesafe.ai`. There is no local Ollama service and no `network_mode: host`.

## Tests

From the project root:

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

These cover daily-window selection, historical-article exclusion, sequential processing of the full window, truncation, processing failure/retry behavior, TypeSafe/Jev classification (mocked `system_one`), score discretization, Why/topic construction from feature labels, and relevance scoring.

They do not call the TypeSafe API.

## Debug classification

Inspect Jev's raw scores, probabilities, confidence, and the discrete values plus Python relevance score without writing to SQLite:

```bash
python -m app.debug_classification --url "https://example.com/article"
python -m app.debug_classification --article-id 123
```

```bash
docker compose run --rm digest python -m app.debug_classification --article-id 123
```

This does not persist classification, probabilities, or article content.

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

Feature logs use readable labels (`Direct:`, `Related:`, `Penalties:`, `Why:`), not raw feature ids.

Logs should not contain full article content. If `TYPESAFE_API_KEY` appears in an exception, it is replaced with `[redacted]`.

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

Do not point the collector cron job at the daily processor. Collection should stay cheap and independent of classification.

## TypeSafe / Jev

Classification uses the official `typesafe-sdk` client (pinned in `requirements.txt`) and one `system_one` call per article. There is no Ollama or local Qwen runtime.

Set in `.env`:

```text
TYPESAFE_API_KEY=...
TYPESAFE_MODEL=jev-1.13.0
```

The default model pin is `jev-1.13.0` (override with `TYPESAFE_MODEL`).

Article title and body are sent to the TypeSafe API for classification. Full article content is not persisted in the local SQLite database. This project does not make claims about TypeSafe's own retention.

If `TYPESAFE_API_KEY` is missing, classification fails with:

```text
TYPESAFE_API_KEY is not configured
```

The collector and digest preview jobs do not use these variables.

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
            failed_at = NULL,
            delivered_at = NULL
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

If neither full content nor a usable excerpt is available, the first attempt records a processing error and the article remains eligible for retry.

If a later attempt still cannot get full content or a usable excerpt, the processor classifies from the title and URL only. The prompt says the article body is unavailable, that only the title and URL are evidence, and that classification must stay conservative and must not infer unsupported details. That prompt is not stored.

Classification errors still increment `processing_attempts`. After the maximum number of attempts, the article is marked with `failed_at` and is no longer selected.

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
classifier request payload (title and content)
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
