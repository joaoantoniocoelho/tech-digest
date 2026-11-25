# Migrating from Railway to the VPS

One-time runbook. After the last step, delete this file. Day-to-day operation
is in [production.md](production.md).

The one thing that must not happen is both deployments running the scheduler
at the same time: each would send the 07:00 digest from its own database. The
order below stops Railway before the VPS starts.

**When:** between 10:15 and 11:45 or between 12:30 and 15:45, Brasília time.
No scheduled job runs in those windows and the morning send is over. Expect a
few minutes in which the signup form answers "unavailable".

Commands marked **[mac]** run on your machine; **[vps]** on the VPS.

## Before the cutover (any day, nothing changes in production)

1. Merge the pull request. Railway redeploys as usual; the new code only adds
   the `author_notes` table.

2. **[vps]** Install Docker and allow your user to use it:

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker "$USER"
   ```

   Log out and in again, then `docker run --rm hello-world`.

3. **[vps]** Create the production checkout and its `.env`:

   ```bash
   mkdir -p ~/apps && cd ~/apps
   git clone https://github.com/joaoantoniocoelho/tech-digest.git
   cd tech-digest && mkdir -p data
   install -m 600 /dev/null .env
   ```

   Fill `.env` with the variables listed in
   [production.md](production.md#configuration). Copy the values from the
   Railway service's Variables tab, except `TUNNEL_TOKEN` (next step). Do not
   copy `PORT`, `TZ`, `DIGEST_DB_PATH` or any `RAILWAY_*` variable.

4. **Cloudflare** → Zero Trust → Networks → Tunnels → create a tunnel named
   `tech-digest` (connector type cloudflared). Copy its token into
   `TUNNEL_TOKEN` in `.env`. Do **not** add the public hostname yet.

5. **[vps]** Build the image, so the cutover does not wait for it:

   ```bash
   cd ~/apps/tech-digest && docker compose -f compose.prod.yaml build
   ```

6. **[mac]** Install and link the Railway CLI:

   ```bash
   brew install railway && railway login
   cd <your tech-digest clone> && railway link
   ```

## Cutover

7. **[mac]** Export a consistent copy of the production database:

   ```bash
   railway ssh -- sh -c 'python -m app.backup --output /tmp/export.db >&2 && base64 /tmp/export.db' \
     < /dev/null | tr -d '\r' | base64 --decode > digest.db
   ```

   Then compare the copy with the live database. Both must print the same
   counts and `integrity: ok`; keep the output for step 10:

   ```bash
   python -m app.backup --check digest.db
   railway ssh -- python -m app.backup --check
   ```

   If the first command does not produce a valid file with your CLI version,
   write the copy to the volume instead
   (`railway ssh -- python -m app.backup --output /data/export.db`) and fetch
   it with `railway volume files download`.

8. **Railway** → the service → Settings → remove the deployment (or scale it to
   zero) so the app is stopped. Keep the project and its volume for now.
   Confirm: `curl -i https://api.digest.joaoac.com/health` no longer answers
   200.

9. **[mac]** Send the database to the VPS:

   ```bash
   scp digest.db vps:apps/tech-digest/data/digest.db
   ```

10. **[vps]** Start production and compare the counts with step 7. They must
    match, `integrity` must be `ok`, and `last_job` must be today:

    ```bash
    cd ~/apps/tech-digest
    docker compose -f compose.prod.yaml up -d
    scripts/prod app.backup --check
    docker compose -f compose.prod.yaml logs --tail 20 app tunnel
    ```

    `last_job` being today is what tells the scheduler that this morning's
    slots already ran. If the counts do not match, stop here: run
    `docker compose -f compose.prod.yaml down`, redeploy on Railway and
    investigate. Nothing has been switched yet.

11. **Cloudflare** → DNS for `joaoac.com` → delete the `api.digest` record that
    points to Railway. Then in the tunnel → Public Hostname → add
    `api.digest.joaoac.com` with service `http://app:8080`.

12. **[mac]** Verify from outside:

    ```bash
    curl -fsS https://api.digest.joaoac.com/health
    curl -fsS https://api.digest.joaoac.com/digests | head -c 300
    ```

    Open `https://digest.joaoac.com`, check that the latest edition loads, and
    subscribe with a test address: the welcome email must arrive and the
    address must appear in `scripts/prod app.backup --check` as one more
    subscriber. Unsubscribe it from the email's link.

## After the cutover

13. **[vps]** Add the daily backup to your crontab (`crontab -e`), using the
    line in [production.md](production.md#backups-and-restore).

14. Turn on automatic deploys:

    - **Tailscale** admin → Access controls: define `tag:ci`, allow it to reach
      the VPS on port 22. Settings → OAuth clients: create one with the
      `auth_keys` write scope and tag `tag:ci`.
    - **[mac]** Create the deploy key: `ssh-keygen -t ed25519 -N "" -f deploy_key -C tech-digest-deploy`.
    - **[vps]** Add it to `~/.ssh/authorized_keys`, restricted to the deploy
      script (one line):

      ```text
      command="cd ~/apps/tech-digest && scripts/deploy",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... tech-digest-deploy
      ```

    - **GitHub** → the repository → Settings → Secrets and variables → Actions:
      add the secrets listed in [production.md](production.md#deploy), then
      the variable `VPS_DEPLOY` = `true`. Delete `deploy_key` from your machine.
    - Run the `deploy` workflow once by hand (Actions → deploy → Run workflow)
      and check that it ends with `deploy: ok`.

15. The next morning, confirm the 07:00 edition arrived exactly once and that
    `docker compose -f compose.prod.yaml logs --since 12h app | grep job_finish`
    shows `collect`, `process` and `send` with `status=ok`.

16. After a few good days: disconnect the repository from Railway and delete
    the Railway project. Then, in one commit, delete `railway.toml` and this
    file, and remove the Railway mentions from `README.md`.

## Rolling back during the cutover

Until step 11 the public hostname still points to Railway. To go back: stop
the VPS stack (`docker compose -f compose.prod.yaml down`) and redeploy the
Railway service. After step 11, also restore the DNS record you deleted.
Subscribers who signed up on the VPS in between exist only in the VPS
database; export them before going back.
