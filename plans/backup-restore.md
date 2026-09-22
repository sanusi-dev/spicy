# Backup and Restore — Implementation Plan

**Status:** proposed (awaiting developer review; not yet in `PLAN.md` / `FEATURES.md`).
**Scope:** two shell scripts + two Make targets + runbook. No Django code, no new
dependency, no backup sidecar container.

## 1. Objective

Survive disk failure, theft, or a bad migration on the single cashier desktop: one command
produces a restorable snapshot (database + uploaded images); one command puts it back.
A Docker volume alone is explicitly not a backup.

## 2. Decisions

- `scripts/backup_db.sh` (new, executable): dumps via the running compose service so no
  new credentials are introduced —
  `docker compose exec -T db pg_dump -U postgres spicy | gzip > backups/spicy-<ts>.sql.gz`
  (`postgres:17` image in `docker-compose.yml:1-16`; db/user from compose, overridable by
  `POSTGRES_DB`/`POSTGRES_USER` env with the same defaults). Same run tars uploads:
  `tar -czf backups/media-<ts>.tar.gz media/`. Prints both absolute paths on success.
- Retention inside the script: `find backups/ -mtime +14 -delete` after a successful run
  (14 daily snapshots; adjust by editing one variable at the top of the script).
- `scripts/restore_db.sh <dump.sql.gz> [--yes]` (new, executable): refuses without the
  file; without `--yes` prints the target database and waits for typed `yes`. Requires
  containers up; refuses while `celery beat` holds the scheduler lock is out of scope —
  instead it warns to stop the dev server first. Implementation: terminate other backends,
  `DROP DATABASE` + `CREATE DATABASE`, `gunzip -c dump | psql`, then `migrate --check`
  equivalent (`manage.py migrate` is idempotent — run it to confirm schema state).
  Media restore is a separate explicit flag `--with-media <tar>` that replaces `media/`
  (never implicit, so a DB-only restore cannot wipe images by accident).
- `Makefile`: `backup` (runs the dump script) and `restore` (passes `$(ARGS)` through to
  the restore script), placed beside `dbshell`/`drop-test-db` (`Makefile:44-48`). Both
  echo the produced/expected paths.
- `.gitignore`: `/backups/` (dumps contain live sales figures and password hashes — never
  committed). Cron appends to `backups/backup.log`, also ignored.
- Schedule (documented, not installed by automation): `30 23 * * * cd <repo> &&
  make backup >> backups/backup.log 2>&1` — after close, before midnight. Off-machine
  copy is part of the runbook, not the script: weekly copy of the newest pair to an
  external drive (a dump on the same disk does not survive theft/fire).
- Verification is a manual runbook, not pytest (scripts live outside Django): after any
  change to either script — `make backup`, confirm both files exist and are non-empty,
  `make drop-test-db`, restore the dump into a scratch database, `manage.py check`,
  spot-check order count and one login. The runbook lists the exact commands.

## 3. Frontend / app code

None. No views, templates, URLs, or models.

## 4. Tests

None in pytest. Acceptance is the runbook drill above, recorded by date + operator in
`backups/RESTORE_DRILLS.md` (ignored by git alongside the dumps).

## 5. Docs (same task)

- New `docs/ops/backup-restore.md`: prerequisites, `make backup` / `make restore`
  usage, cron line, external-copy routine, restore-drill checklist, failure table
  (db container down, disk full, wrong file, lock contention) with the exact error and fix.
- `docs/README.md` index gains the ops page. `FEATURES.md` D section gains one row:
  "Nightly database + media snapshots with a tested restore."
