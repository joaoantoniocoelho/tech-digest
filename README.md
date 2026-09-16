# Tech Digest

A lightweight, self-hosted tech news digest running on my home server.

The goal is simple: collect articles from RSS/Atom feeds, store only new entries, and eventually generate a curated daily digest based on the topics I care about.

The project is intentionally small and local-first.

## Current Features

* RSS and Atom feed collection
* Configurable sources through YAML
* SQLite persistence
* URL-based deduplication
* Dockerized execution
* Periodic collection using cron
* Simple execution logs with timestamps

## Architecture

```text
RSS / Atom feeds
       |
       v
   Collector
       |
       v
     SQLite
       |
       v
Future processing
       |
       v
Daily digest
```

The collector runs as a short-lived Docker job instead of a continuously running service.

The host periodically starts the container, collects new articles, persists them to SQLite, and exits.

## Project Structure

```text
tech-digest/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── db.py
│   └── rss.py
├── config/
│   └── sources.yaml
├── data/
├── logs/
├── Dockerfile
├── compose.yaml
├── requirements.txt
└── README.md
```

## Sources

Feeds are configured in:

```text
config/sources.yaml
```

Example:

```yaml
sources:
  - name: Hacker News
    url: https://news.ycombinator.com/rss

  - name: Simon Willison
    url: https://simonwillison.net/atom/everything/
```

Adding a new source only requires adding another feed to this file.

## Running Locally

Create a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the collector:

```bash
python -m app.main
```

## Running with Docker

Build the image:

```bash
docker compose build
```

Run the collector:

```bash
docker compose run --rm digest
```

The SQLite database is persisted on the host through:

```text
./data/digest.db
```

so recreating the container does not remove collected articles.

## Scheduling

The collector is designed to run periodically instead of staying online continuously.

For example, the following cron entry runs it every hour:

```cron
0 * * * * cd /path/to/tech-digest && /usr/bin/docker compose run --rm digest >> logs/collector.log 2>&1
```

Logs can then be inspected with:

```bash
tail -n 50 logs/collector.log
```

## Database

Articles are stored in SQLite with fields such as:

```text
id
source
title
url
published_at
discovered_at
```

Article URLs are unique, which prevents the same item from being stored multiple times.

## Roadmap

The next steps are:

* Fetch and extract the full article content
* Improve feed and network error handling
* Rank articles by relevance
* Generate summaries locally using Ollama
* Build a daily digest
* Deliver the digest through Telegram
* Add optional feedback-based personalization

## Philosophy

This project is intentionally simple.

No Kubernetes, no message queues, no external database, and no unnecessary infrastructure.

The core stack is:

```text
Python
SQLite
Docker
Cron
```

More components will only be added when they solve an actual problem.

## License

Personal project. License TBD.
