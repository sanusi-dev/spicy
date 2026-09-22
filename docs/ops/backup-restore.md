# Backup and Restore

One command snapshots the restaurant database plus uploaded images on the cashier desktop; one command puts them back. A Docker volume alone is not a backup.

## Prerequisites

- The `db` compose service runs (`make start-bg`); no new credentials — the scripts use `POSTGRES_DB`/`POSTGRES_USER` from the environment, defaulting to `spicy`/`postgres` like `docker-compose.yml`.
- Snapshots land in `backups/` (gitignored, never committed — dumps contain sales figures and password hashes).

## Usage

```bash
make backup
# Database snapshot: /abs/path/backups/spicy-2026-09-11-233000.sql.gz
# Media snapshot: /abs/path/backups/media-2026-09-11-233000.tar.gz

make restore ARGS='backups/spicy-2026-09-11-233000.sql.gz'
# Without --yes, prints the target database and waits for a typed `yes`.
# Media is never touched unless passed explicitly:
make restore ARGS='backups/spicy-2026-09-11-233000.sql.gz --yes --with-media backups/media-2026-09-11-233000.tar.gz'
```

The restore terminates other backends, drops and recreates the database, loads the dump (`ON_ERROR_STOP=1`), then runs `manage.py migrate` to confirm the schema state. Media restore extracts over `media/` only when `--with-media` is given, so a DB-only restore cannot wipe images by accident.

Retention: each successful backup deletes `spicy-*.sql.gz` / `media-*.tar.gz` older than 14 days. The log and drill records are never auto-deleted.

## Schedule

Documented, not installed by automation — after close, before midnight:

```cron
30 23 * * * cd <repo> && make backup >> backups/backup.log 2>&1
```

Off-machine copy (weekly): copy the newest `spicy-*.sql.gz` + `media-*.tar.gz` pair to an external drive. A dump on the same disk does not survive theft or fire.

## Restore-drill checklist

After any change to either script:

1. `make backup` — confirm both files exist and are non-empty.
2. Restore the dump into a scratch database (never the live one for a drill):
   `docker compose exec db psql -U postgres -d postgres -c "CREATE DATABASE spicy_drill"` then
   `gunzip -c backups/spicy-<ts>.sql.gz | docker compose exec -T db psql -U postgres -d spicy_drill`,
   then drop it.
3. `uv run manage.py check`.
4. Spot-check the order count and one login against the live figures.
5. Record the date + operator in `backups/RESTORE_DRILLS.md` (gitignored).

## Failure table

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot connect to the Docker daemon` / compose error | Docker or the `db` container is down | `make start-bg`, wait for healthy, retry |
| `pg_dump: error` / empty `.sql.gz` | Wrong `POSTGRES_DB`/`POSTGRES_USER`, or db unhealthy | Verify with `make dbshell`; export the matching env values and retry |
| `No space left on device` | Disk full mid-dump | Free space, delete old snapshots, retry; the partial file is left for inspection |
| `Media archive not found` / `Usage:` | Wrong path or missing `<dump.sql.gz>` | Pass an existing `backups/spicy-*.sql.gz` path |
| Restore hangs or `database is being accessed by other users` | Dev server / beat still connected | `make stop`, then re-run (the script terminates backends once, then proceeds) |
| `Aborted.` | Anything other than `yes` was typed | Re-run and type `yes`, or pass `--yes` |
