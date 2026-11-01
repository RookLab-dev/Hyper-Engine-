# Hyper-Engine — Database backup & recovery (P6)

Single SQLite file (default `storage/hyper_engine.db`), versioned schema
(`schema_version` table, current v2), DELETE journal mode,
`synchronous=FULL` (a committed risk-state write — including a latched
kill — survives process crash, OS crash, and power loss), foreign-key
enforcement on. Full contract in `src/core/database.py` and
`src/core/db_migrations.py`.

## Backup

- **Use `Database.backup_to(dest)`** — SQLite's online backup API through
  a consistent snapshot while the source stays open. It creates missing
  parent directories; an existing destination is overwritten by SQLite.
- **Never copy a live database with a plain file copy.** File copies of
  an open SQLite database risk torn pages and stale reads (and, under
  other journal modes, orphaned sidecars). If the loop is fully stopped
  and the process exited, a file copy of the closed database is safe —
  but `backup_to()` is the recommended procedure either way.
- Back up before VPS deployment, before any manual intervention, and
  after operator kill resets. Keep more than one generation.
- If `backup_to()` raises while the destination already exists, do NOT
  assume that file is a fresh successful backup — it is the previous
  contents (or a partially overwritten file), never validated by this
  run. Before relying on it, open it via `Database()` (startup
  verification fails closed on anything torn), check
  `get_schema_version()`, and spot-check `risk_state` rows (kill
  flags, counters, PnL, position intent) against the JSONL logs. When
  in doubt, fall back to an older known-good generation.

## Recovery

1. Stop all trading loops.
2. Restore the backup file to `storage/hyper_engine.db` (or point
   `Database(db_path=...)` at the copy for inspection first).
3. Open it via `Database()` — startup verifies the schema version,
   table/column completeness, journal mode (`delete`), and foreign-key
   enforcement. Any mismatch raises `DatabaseSchemaError` and the loops
   refuse to trade (fail closed).
4. Check `get_schema_version()`, spot-check `risk_state` rows (kill
   flags, counters, PnL, position intent) against the JSONL logs.
5. If the file is stamped with a version newer than the build, upgrade
   Hyper-Engine first — the engine never downgrades silently.
6. If verification fails on a torn copy, restore an older generation —
   never hand-edit the database to force it open.

## VPS notes

- DELETE journal mode is deliberate: no `-wal`/`-shm` sidecars to lose
  in a crash or confuse a file-based backup. The `.gitignore` covers
  `storage/`, `*.db`, and `*.db-journal` — the database never belongs
  in version control.
- SQLite here is single-process: one loop per database file. Do not run
  two trading loops against the same file concurrently.
- `busy_timeout` is 5000 ms against brief contention (e.g. dashboard
  reads during a write); sustained lock contention means something else
  is writing and should be investigated, not tuned away.
