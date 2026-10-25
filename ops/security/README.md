# RemoteImpact recovery and security operations

Backups run **weekly**, Sunday at 03:10 UTC, through
`remoteimpact-backup.timer`. Each complete set contains an encrypted PostgreSQL
custom-format dump, media archive and configuration archive. Eight complete weekly
sets are retained on the server. A failed run does not prune the previous sets.

The encryption recipient is on the VPS; the private decryption identity remains on
the owner's Mac at `~/.config/remoteimpact-backups/identity.txt`. The restricted SSH
pull key can only list and fetch encrypted backup files; it cannot execute shell
commands or read plaintext data. Keep the decryption identity in a trusted recovery
vault as well; losing every copy makes the encrypted backups unrecoverable.

The Mac LaunchAgent `org.remoteimpact.backup-pull` runs Sunday at 07:30 local time
and at login. It only pulls completed weekly sets and verifies their SHA-256
checksums. It never starts another server backup. Files are stored in
`~/Documents/RemoteImpact Backups`. An offline Mac cannot receive a new copy until
it next runs the pull job; check the backup timer and Mac pull logs after failures.

Restore to a separate, network-isolated database first. Decrypt locally and stream
into an isolated `pg_restore --no-owner --no-acl --exit-on-error` process. Validate
table and application counts and run application checks before considering a
production restore. Database recovery never runs as part of application rollback.
Do not put decrypted database dumps, keys, CVs, secrets or configuration archives
in GitHub or public artifacts.

The 25 October 2026 remediation performed a full isolated restore, validating 99
tables, 30,009 jobs, 3,035 users and 131 recorded migrations. These counts describe
that recovery snapshot only and will change as the platform is used.

Genpilot onboarding and customer agent gateways on this VPS were stopped with the
owner's explicit approval. Their services must remain disabled until they have a
separate host and a reviewed privilege boundary. A missing
`/etc/remoteimpact/genpilot-isolated-approved` marker prevents accidental starts;
do not create it merely to bypass this isolation requirement.

`ensure_active.py` is installed as `/usr/local/sbin/ri-ensure-active` and called
after Docker starts by a systemd drop-in. It only starts the recorded database,
cache and active immutable release when stopped, then checks backend health. It
never rebuilds containers, changes the proxy, runs migrations or selects a release
while a pending cutover exists. Retained rollback containers remain stopped.

Public JavaScript uses Alpine's CSP build. Executable template blocks and native
event handlers are explicitly marked with `csp_script` and `csp_handler`; component
factories use `csp_component` and register through Alpine.data. Middleware verifies
server signatures before adding content hashes. Cached HTML authorization is bound
to exact script contents. Repository-authored static fragment handlers are included
in the initial policy for HTMX. JSON data blocks are not executable script blocks.
New dynamic fragment code must be loaded through an allowed first-party script or
included in the initial document policy. Do not authorize arbitrary rendered HTML
or restore unsafe-inline/unsafe-eval to fix a missing template annotation.
