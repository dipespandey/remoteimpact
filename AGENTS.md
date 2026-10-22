# Production deployment

Remote Impact uses blue-green releases on the VPS. Read `ops/deploy/README.md` before deploying.

- Production code is frozen in each release image. Updating `/opt/easyclaw/repo` alone does not update the serving application.
- After installing reviewed source changes on the host, deploy with `/opt/easyclaw/deploy.sh`. It builds a candidate, checks it and switches Caddy. Use `--check-only` for a candidate without cutover; use `rollback` to restore the retained application release.
- Do not deploy by sending Gunicorn HUP, restarting/recreating the serving container with Compose, or copying files directly into a serving container. Frozen releases ignore HUP.
- Scheduled management commands must use `/opt/easyclaw/exec-active`, which selects the active release and protects running jobs during retirement.
- The VPS currently has substantial uncommitted application changes. Preserve them. Do not run git reset/pull/checkout over them to unblock CI. GitHub commit-based deployment deliberately refuses until that source drift is reconciled.
- Review migrations for compatibility with both releases before using `--allow-migrations`. Application rollback never reverses database schema or data.

## LinkedIn publishing identity

Always publish Remote Impact LinkedIn content as the **RemoteImpact company page** (company ID `110458208`, author `urn:li:organization:110458208`). Never publish it from a personal profile. Verify the company identity in the composer before submitting. This is the user’s explicit standing preference.
