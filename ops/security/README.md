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
