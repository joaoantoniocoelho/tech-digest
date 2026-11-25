# Production

Tech Digest runs on a single VPS as two containers defined in
`compose.prod.yaml`:

- `app`: the HTTP API and the in-process scheduler (`python -m app.server`).
- `tunnel`: `cloudflared`, which publishes the app at
  `https://api.digest.joaoac.com` through a Cloudflare Tunnel.

No port is open on the host. The public site (`digest.joaoac.com`, in the
`joaoac.com` repository, on Cloudflare Workers) calls this API.

Everything below runs on the VPS, as the user that owns the checkout, from the
production checkout:

```text
~/apps/tech-digest          production checkout, always on master, never edited by hand
~/apps/tech-digest/.env     secrets and configuration (not in git)
~/apps/tech-digest/data/    digest.db and backups/ (not in git)
```

Development happens in a different clone. Do not develop, switch branches or
leave local changes in the production checkout: `scripts/deploy` refuses to run
there if it is not a clean `master`.

## What runs by itself

Nothing needs to be started by hand after a reboot or a deploy.

| What | When (America/Sao_Paulo) | How |
| --- | --- | --- |
| Collect feeds | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 and 06:45 | scheduler inside `app` |
| Classify and score | 06:50 | scheduler inside `app` |
| Send the digest | 07:00 | scheduler inside `app` |
| Restart after crash or reboot | always | `restart: unless-stopped` |
| Deploy | every push to `master` that passes `check` | `.github/workflows/deploy.yml` |
| Database backup | before every deploy, and daily by cron | `python -m app.backup` |

Each scheduled slot runs once and is recorded in `job_runs`. A slot missed
while the app was down still runs if the app is back within 30 minutes
(collect) or 3 hours (process and send). Only one job runs at a time
(`pipeline_lock`).

## Everyday commands

`scripts/prod <module> [args]` runs a Python module inside the running `app`
container.

```bash
cd ~/apps/tech-digest

# Is it healthy?
docker compose -f compose.prod.yaml ps
curl -fsS https://api.digest.joaoac.com/health

# Data at a glance: integrity, counts, last edition, last finished job
scripts/prod app.backup --check

# Logs (one line per event: job_start, job_finish, email_send, http, ...)
docker compose -f compose.prod.yaml logs --since 24h app
docker compose -f compose.prod.yaml logs --since 24h app | grep -E "event=job_|event=digest_send"
docker compose -f compose.prod.yaml logs -f app

# Authorial notes (see editorial.md)
scripts/prod app.notes list
scripts/prod app.notes save - < note.yaml
scripts/prod app.notes approve <slug>

# Preview today's selection without sending or marking anything
scripts/prod app.digest
```

### Running a job by hand

These take the same lock as the scheduler and exit with an error if a job is
already running.

```bash
scripts/prod app.main            # collect feeds
scripts/prod app.process_daily   # classify and score
scripts/prod app.send_digest     # SENDS REAL EMAIL to every active subscriber
```

`app.send_digest` is not a dry run. Only run it when a human asked for a send.
If the 07:00 slot already sent, a second run builds a new email from articles
not yet delivered and sends it to everyone again.

## Deploy

Pushing to `master` deploys by itself once `check` passes. The workflow joins
the tailnet, opens an SSH session with a key that can only run
`scripts/deploy`, and the script:

1. fast-forwards the production checkout to `origin/master`;
2. waits (up to 15 minutes) for a running pipeline job to finish, so a send is
   never cut in half;
3. writes a database backup;
4. rebuilds and restarts the containers;
5. waits for the app to report healthy, and fails with the last log lines if it
   does not.

To deploy by hand, run the same script on the VPS:

```bash
cd ~/apps/tech-digest && scripts/deploy
```

To roll back, revert the commit on `master` and let it deploy. The schema only
ever gains tables and columns, so older code keeps working on a newer database.

The workflow only runs when the repository variable `VPS_DEPLOY` is `true`. It
needs these repository secrets:

| Secret | Value |
| --- | --- |
| `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET` | Tailscale OAuth client with the `auth_keys` scope and tag `tag:ci` |
| `VPS_HOST` | the VPS tailnet name or address |
| `VPS_USER` | the user that owns `~/apps/tech-digest` |
| `VPS_SSH_KEY` | private key of the deploy key pair |
| `VPS_KNOWN_HOSTS` | output of `ssh-keyscan -t ed25519 <VPS_HOST>` |

The tailnet policy must let `tag:ci` reach the VPS on port 22.

## Configuration

`.env` in the production checkout, mode `600`:

```text
TYPESAFE_API_KEY=...
TYPESAFE_MODEL=...
RESEND_API_KEY=...
RESEND_FROM=Tech Digest <digest@digest.joaoac.com>
PUBLIC_BASE_URL=https://api.digest.joaoac.com
DIGEST_JOB_TOKEN=...
DIGEST_REVALIDATE_TOKEN=...        # same value as the site's secret
TELEGRAM_BOT_TOKEN=...             # optional operator alerts
TELEGRAM_CHAT_ID=...
TUNNEL_TOKEN=...                   # Cloudflare Tunnel token, used by the tunnel container
```

`PORT`, `TZ` and `DIGEST_DB_PATH` are fixed in `compose.prod.yaml`. After
editing `.env`, apply it with:

```bash
docker compose -f compose.prod.yaml up -d
```

## Backups and restore

`python -m app.backup` writes a consistent copy to
`data/backups/digest-<UTC timestamp>.db`, safe while the app is running, and
keeps the newest 14. The daily one comes from the user's crontab:

```cron
30 3 * * * cd $HOME/apps/tech-digest && scripts/prod app.backup >> $HOME/apps/tech-digest/data/backup.log 2>&1
```

These copies live on the same disk as the database. Copy them off the VPS if
losing the machine must not lose the subscriber list.

To restore, stop the app so nothing writes during the swap:

```bash
cd ~/apps/tech-digest
docker compose -f compose.prod.yaml stop app
sudo cp data/backups/digest-<timestamp>.db data/digest.db
sudo rm -f data/digest.db-wal data/digest.db-shm
docker compose -f compose.prod.yaml up -d
scripts/prod app.backup --check
```

Files under `data/` belong to root because the container writes them.

## When something is wrong

| Symptom | Check |
| --- | --- |
| No email this morning | `logs --since 24h app \| grep -E "job_\|digest_send"`. `reason=no_articles` means nothing passed the threshold: that is normal, not a failure. `job_finish job=send status=error` has the redacted error. |
| Site says the service is unavailable | `docker compose -f compose.prod.yaml ps`: both containers must be running and `app` healthy. Then `logs --tail 50 tunnel`. |
| `health` is 503 | The database cannot be opened. Check disk space (`df -h`) and `scripts/prod app.backup --check`. |
| A job never finishes | The lock expires by itself 90 seconds after a job stops sending heartbeats; the next slot then runs. |
| Deploy workflow failed | Read its log; it prints the last 50 app log lines when the app does not become healthy. The previous containers keep running if the build fails. |
| Edition page on the site is stale | Revalidation failed (`event=digest_revalidate status=error`). The site refreshes by itself within an hour. |

Do not edit `data/digest.db` by hand and do not delete rows to "retry" a send.
