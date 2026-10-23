# Production deployment

Remote Impact uses blue-green releases on the VPS. Read `ops/deploy/README.md` before deploying.

- Production code is frozen in each release image. Updating `/opt/easyclaw/repo` alone does not update the serving application.
- Production changes deploy through the GitHub workflow from an exact tested main SHA. The host builds an immutable candidate, checks it and switches Caddy. Use `rollback` to restore the retained application release.
- Do not deploy by sending Gunicorn HUP, restarting/recreating the serving container with Compose, or copying files directly into a serving container. Frozen releases ignore HUP.
- Scheduled management commands must use `/opt/easyclaw/exec-active`, which selects the active release and protects running jobs during retirement.
- Preserve the historical uncommitted VPS checkout. Do not run git reset/pull/checkout over it. GitHub releases use the separate bare source cache and archive their exact commit; the mutable checkout is not a production deployment source.
- Review migrations for compatibility with both releases before using `--allow-migrations`. Application rollback never reverses database schema or data.

## LinkedIn publishing identity

Always publish Remote Impact LinkedIn content as the **RemoteImpact company page** (company ID `110458208`, author `urn:li:organization:110458208`). Never publish it from a personal profile. Verify the company identity in the composer before submitting. This is the user’s explicit standing preference.

## GitHub-only release policy

All new production application changes must go through a pull request to `main` and the GitHub deployment workflow. Required SQLite/PostgreSQL tests, migration drift/fresh migration checks and frontend/deployment checks must pass before merging. Deploy the exact checked SHA via the restricted GitHub SSH entrypoint; do not use a manual snapshot deployment or copy changes into the mutable VPS checkout. Runtime build changes belong in `ops/deploy/Dockerfile.runtime`. Pending production migrations fail closed unless a reviewed workflow dispatch explicitly permits them. Preserve the stopped rollback container and scheduled-job drain locks.
