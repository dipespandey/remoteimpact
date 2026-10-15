# Remote Impact blue-green releases

The production host is `/opt/easyclaw` on the existing VPS. Caddy serves one active immutable web container. PostgreSQL, uploaded media and credentials remain shared; the database is never recreated by deployment. Staging on port 8200 is separate.

## Everyday use

```sh
/opt/easyclaw/deploy.sh                 # freeze current server source, build, check, switch
/opt/easyclaw/deploy.sh --check-only    # isolated candidate on 8103, no traffic switch or data sync
/opt/easyclaw/deploy.sh status
/opt/easyclaw/deploy.sh rollback        # restore retained app; never reverses database migrations
/opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py check
```

Legacy `fast`, `build` and `migrate` mode names are accepted, but all now use isolated releases. Editing the server repo or sending Gunicorn HUP does **not** update the frozen serving code. Finish the file update, then use `deploy.sh`. Never run `docker compose up web` as a production deployment: the original Compose definition belongs to the legacy setup, not release orchestration. Keep using Compose for the database only when needed.

A deployment lock rejects overlapping manual/CI deploys. GitHub also queues production jobs without cancelling a running release. GitHub fetches and deploys the exact tested commit with `--ref SHA`; it does not `git pull` the live source. The host currently contains extensive manually deployed, uncommitted application changes. **Commit-based CI deployment intentionally refuses to replace those until the server source is reconciled with Git.** Manual snapshot deployment preserves them. Do not discard them merely to make CI green.

The host tools are installed with `python3 /opt/easyclaw/repo/ops/deploy/install.py`. This backs up the old deploy script and cron files, installs the new entrypoints, and changes only scheduled-command container selection. Cron schedules and command arguments stay the same. The wrapper resolves the active container ID and holds a per-container job lock, so a release cannot be retired during its job. The active container also retains the familiar `easyclaw-web-1` name for compatibility; the ID in state is authoritative.

## Release sequence

1. Freeze a clean Git archive or snapshot the server source, excluding credentials, media, virtual environments, marketing and caches. Verify a manual copy did not change during copying.
2. Build an immutable release image, reusing a dependency image keyed by the production Dockerfile and dependency files. Rebuild dependencies when that key changes. Remove old app source from the base before copying the new snapshot.
3. Preserve old hashed static assets, collect new assets at build time, and mount only persistent media plus the existing indexing credential file at runtime. Never mount mutable application code into releases.
4. Run Django checks and `migrate --check`. Start the candidate on loopback 8101/8102. Probe `/healthz/` (database and cache), `/`, `/jobs/` and static files. These GET probes send no messages and create no payments.
5. Run the existing organization-plan synchronization, validate Caddy, atomically update its config and gracefully reload. Verify the local TLS route and expected release response header, bypassing CDN ambiguity.
6. On a failed post-cutover probe, restore the previous route. Keep the previous container running for in-flight requests and rollback. Before the next deployment reuses its port, require no running scheduled jobs and gracefully stop it. No automatic volume or image pruning.

The release header is `X-RemoteImpact-Release`. State lives in `releases/state.json`; each release has its own source snapshot and `release.json`. Caddy changes affect only the `remoteimpact.org` upstream and release header. Other sites and staging are preserved.

## Database changes

By default any pending migration blocks the deployment **before traffic changes**. After reviewing the plan and compatibility with both app versions:

```sh
/opt/easyclaw/deploy.sh --allow-migrations
```

Use expand/contract migrations: add nullable/default-compatible columns or new tables first; deploy code; backfill separately; remove old columns in a later release after rollback compatibility is no longer needed. Review `RunPython`, data rewrites, constraint changes, indexes and table locks individually. The flag is an operator assertion of compatibility, not an automatic proof. Deployment sets PostgreSQL lock timeout to 5 seconds and statement timeout to 60 seconds for one-off management commands; large backfills/index changes need a separate plan. An applied migration is not undone if the app release fails. Check-only refuses migration execution.

## Recovery and limits

If interrupted during cutover, `pending.json` blocks further deployment. Inspect it, `state.json`, the live Caddy upstream/header and the running containers. Restore the known healthy route, validate/reload Caddy, then reconcile state and remove `pending.json`. Never delete a live container just to clear this guard. Tooling and cron installation backups are under `backups/blue-green-*`.

Ordinary compatible releases should not require planned downtime. A single VPS/database failure, OOM, incompatible migration or an untested application defect can still disrupt service. Retained releases and build caches consume disk; inspect `docker system df` and release directories, then remove only known obsolete release images/snapshots after checking active/previous IDs. Never prune shared volumes.

Run safety tests with `python3 -m unittest discover -s ops/deploy -p 'test_*.py'`.
