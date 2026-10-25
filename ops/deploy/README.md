# Remote Impact blue-green releases

The production host is `/opt/easyclaw` on the existing VPS. Caddy serves one active immutable web container. PostgreSQL, uploaded media and credentials remain shared; the database is never recreated by deployment. Staging on port 8200 is separate.

## Everyday use

```sh
/opt/easyclaw/deploy.sh --ref SHA       # restricted GitHub entrypoint uses the checked main SHA
/opt/easyclaw/deploy.sh --ref SHA --check-only  # isolated candidate, no traffic switch or data sync
/opt/easyclaw/deploy.sh status
/opt/easyclaw/deploy.sh rollback        # restore retained app; never reverses database migrations
/opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py check
```

Legacy `fast`, `build` and `migrate` mode names are accepted, but all now use isolated releases. Editing the server repo or sending Gunicorn HUP does **not** update the frozen serving code. Frozen releases ignore HUP; each candidate is tested to ensure its workers stay running when it receives that signal. Commit changes through a pull request and let the GitHub workflow deploy them. Never run `docker compose up web` as a production deployment: the original Compose definition belongs to the legacy setup, not release orchestration. Keep using Compose for the database only when needed.

A deployment lock rejects overlapping manual/CI deploys. GitHub also queues production jobs without cancelling a running release. GitHub fetches and deploys the exact tested commit with `--ref SHA`; it does not `git pull` the live source. The historical host checkout contains manually deployed changes and is preserved for recovery. GitHub releases archive their exact SHA from a separate bare source cache. Ordinary snapshot deployments are disabled after adoption; never discard or reset the historical checkout to make CI green.

The host tools are installed with `python3 /opt/easyclaw/repo/ops/deploy/install.py`. This backs up the old deploy script and cron files, installs the new entrypoints, and changes only scheduled-command container selection. Cron schedules and command arguments stay the same. The wrapper resolves the active container ID and holds a per-container job lock, so a release cannot be retired during its job. The active container also retains the familiar `easyclaw-web-1` name for compatibility; the ID in state is authoritative.

## Release sequence

1. Freeze the exact checked Git SHA from the source cache, excluding credentials, media and caches. Production does not deploy the mutable checkout.
2. Build an immutable release image, reusing a dependency image keyed by the production Dockerfile and dependency files. Rebuild dependencies when that key changes. Remove old app source from the base before copying the new snapshot.
3. Preserve old hashed static assets, collect new assets at build time, and mount only persistent media plus the existing indexing credential file at runtime. Never mount mutable application code into releases.
4. Run Django checks, migration drift detection, the migration plan and `migrate --check`. Start the candidate on loopback 8101/8102. Probe `/healthz/` (database and cache), `/`, `/jobs/` and static files. These GET probes send no messages and create no payments.
5. Run the existing organization-plan synchronization, validate Caddy, atomically update its config and gracefully reload. Verify the local TLS route and expected release response header, bypassing CDN ambiguity.
6. On a failed post-cutover probe, restore the previous route. After successful cutover, gracefully stop the previous container when its scheduled jobs finish; retain it for rollback. No automatic volume or image pruning.

The release header is `X-RemoteImpact-Release`. State lives in `releases/state.json`; each release has its own source snapshot and `release.json`. Caddy changes affect only the `remoteimpact.org` upstream and release header. Other sites and staging are preserved.

## Database changes

By default any pending migration blocks the deployment **before traffic changes**. After reviewing the plan and compatibility with both app versions:

```sh
gh workflow run deploy.yml --ref main -f allow_migrations=true
```

Use expand/contract migrations: add nullable/default-compatible columns or new tables first; deploy code; backfill separately; remove old columns in a later release after rollback compatibility is no longer needed. Review `RunPython`, data rewrites, constraint changes, indexes and table locks individually. The flag is an operator assertion of compatibility, not an automatic proof. Deployment sets PostgreSQL lock timeout to 5 seconds and statement timeout to 60 seconds for one-off management commands; large backfills/index changes need a separate plan. An applied migration is not undone if the app release fails. Check-only refuses migration execution.

## Recovery and limits

If interrupted during cutover, `pending.json` blocks further deployment. Inspect it, `state.json`, the live Caddy upstream/header and the running containers. Restore the known healthy route, validate/reload Caddy, then reconcile state and remove `pending.json`. Never delete a live container just to clear this guard. Tooling and cron installation backups are under `backups/blue-green-*`.

Ordinary compatible releases should not require planned downtime. A single VPS/database failure, OOM, incompatible migration or an untested application defect can still disrupt service. Retained releases and build caches consume disk; inspect `docker system df` and release directories, then remove only known obsolete release images/snapshots after checking active/previous IDs. Never prune shared volumes.

Run safety tests with `python3 -m unittest discover -s ops/deploy -p 'test_*.py'`.

## GitHub-only production deployment

Production changes go through a pull request to protected `main`. Both SQLite and PostgreSQL checks must pass, including schema drift detection, fresh-database migration application, the full Django test suite, deployment safety tests, production settings checks and the reproducible frontend build. Only the exact checked main commit is deployed. SSH uses a pinned host key and a restricted command; deployment credentials are unavailable to pull-request checks.

The host keeps a separate bare source cache at `/opt/easyclaw/github-source.git`. GitHub deployment archives the requested SHA from that cache. The old mutable `/opt/easyclaw/repo` checkout is preserved for recovery and is not a deployment source. The runtime Dockerfile is versioned at `ops/deploy/Dockerfile.runtime`, including the patched Node runtime for Playwright. With `/opt/easyclaw/require-github-deploy` present, ordinary snapshot deployments are refused.

Normal pushes never opt in to production schema changes. Pending migrations stop before traffic switches. After reviewing the printed production migration plan for backward compatibility, run this workflow manually on `main` with `allow_migrations` enabled; all CI checks run again before deployment. Never approve destructive or long-locking changes without a separate reviewed rollout. Check-only never applies migrations.

After cutover, the previous release stops automatically when idle. Commands already in progress keep their drain lock; the last command to finish requests cleanup. Rollback starts it and verifies readiness before changing traffic. Retirement handles already-stopped containers and still refuses running management commands or scheduled-job locks. Staging remains independent.


## Scout performance services

A checked release provisions a private, digest-pinned Redis cache (`ri-cache`),
limited to 256 MiB of keys / a 512 MiB container, with LRU eviction and persistent
AOF storage under `/opt/easyclaw/redis-data`. No host port is published. Credentials
and generated runtime configuration stay in root-only files under `secrets/`.
Application containers share the cache and bounded performance logs across releases;
file-cache fallback has separate page/extraction/Scout budgets. Redis client network
operations have one-second connect/read timeouts; Redis is a runtime dependency,
not a durable store of user accounts or queue ownership.

`release_supervisor.py` runs web and one Scout preview worker in the same immutable
application container. Worker inference threads are limited to preserve web CPU.
The worker claims new tasks only while its release is active in the shared read-only
state file. Shutdown forwards TERM and allows 55 seconds for bounded work to drain.
An unexpected child exit stops both children so Docker can restart a coherent pair.
Scheduled commands remain protected by `exec-active` locks. The persistent Scout
worker is excluded from the old unwrapped-management-command check because it has
its own bounded task lease, token fencing and graceful shutdown.

Migration 0085 is additive: three new Scout queue/access/guard tables and a seed
singleton. It does not rewrite existing tables or data. Old releases ignore the new
tables and remain valid for application rollback; leave the migration applied when
rolling back. Review its SQL and both database checks before the explicit migration
workflow dispatch. The worker readiness check runs after migrations and before
candidate startup. No queue worker or Redis is created during check-only inspection.

Completed preview results are refreshed after 180 seconds, current listing availability
and source fingerprints are checked before display, and durable tasks/grants expire
after 24 hours. At most 20 tasks can be queued/running; each session has five enqueues
per ten minutes. A task has at most two attempts, a 40-second deadline and a 45-second
lease. Status and result responses are session-bound and private/no-store.

Read observed performance through the active release:

```sh
/opt/easyclaw/exec-active /app/.venv/bin/python /app/manage.py perf_report /var/log/remoteimpact/performance.jsonl --since-hours 24
```

The report gives route and Scout-stage p50/p95 and separates cached/fresh request
timings. It reads a bounded recent tail; counts are samples, not unique users or a
sequential funnel. The JSONL writer locks rotation across processes and limits logs
to one 10 MiB active file plus three backups, with 0600 permissions. It records UTC
timestamps, route templates, fixed stage names, numeric timings and outcomes; never
raw URLs, briefs, prompts, emails, user identifiers or provider payloads.


## Security boundaries

The serving image runs as UID/GID 10001 with a read-only root filesystem, dropped
Linux capabilities, no-new-privileges, bounded memory/PIDs and writable temporary
filesystems. Only media, application cache and performance logs are writable host
mounts. The host must provision these three directories for UID 10001 and make the
indexing credential bind mount readable by this user without exposing its parent.
Release state contains no credentials and is readable by the queue worker.

The web environment uses a restricted PostgreSQL runtime role. The root-only
`/opt/easyclaw/secrets/migrations.env` supplies DDL credentials only to one-off
`migrate` commands; it is never passed into the serving container. Migration
changes still require explicit reviewed `allow_migrations` dispatch. Runtime and
staging credentials and databases must differ.

Origin requests must pass the Caddy Cloudflare source-address check. Caddy replaces
`X-RemoteImpact-Client-IP`; `TRUSTED_PROXY_CIDRS` names only loopback and the exact
Docker host gateway, not arbitrary private networks. Allauth and API throttling use
the resulting validated transport identity.

Staff must enroll TOTP and retain recovery codes. An existing password-only staff
session cannot enter admin until it completes MFA, and MFA is required again after
eight hours. `MFA_ENCRYPTION_KEY` is a separate root-provisioned encryption key and
must be included in encrypted configuration backups. Enrollment secrets must never
be generated or confirmed on behalf of a staff member. Tests use explicit synthetic
sessions; production enforcement remains on.
