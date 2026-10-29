# Backups and restore drill

A backup only counts once a restore has been rehearsed. This page says what is backed up, how to take a backup,
how to prove it works, and how to recover production.

## What needs protecting

| Data | Where it lives | Covered by Supabase's own backups? |
| --- | --- | --- |
| Accounts, campaigns, characters, maps, battles, dice rolls, upload ledger | Postgres, schema `public` | Pro/Team/Enterprise: daily backups (7/14/30 days). **Free: none.** |
| Uploaded images (avatars, sheets, maps) | Storage bucket `ledger-uploads` | **No.** Database backups hold only the object *metadata*, never the files. |
| Staging and export files | Bucket `ledger-temp` | Not needed; deleted automatically (`scripts/cleanup_temp.py`). |

So whichever plan you are on, the images need their own copy, and on the Free plan so does the database.
Source: [Supabase: Database Backups](https://supabase.com/docs/guides/platform/backups).

## Take a backup

`scripts/backup.py` writes one folder holding a `pg_dump` of the database, every uploaded image and a `manifest.json`
(migration level, row count of every table, checksum of every file).

```
export BACKUP_DATABASE_URL="postgresql://postgres:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres"   # direct or session pooler
export SUPABASE_URL="https://PROJECT_REF.supabase.co"  SUPABASE_SECRET_KEY="sb_secret_..."
python scripts/backup.py --out ~/ledger-backups --keep 8
```

* Use the **direct** or **session-pooler** connection string, not the transaction pooler (port 6543) that Vercel uses.
* `pg_dump` must be the same major version as the server or newer (Supabase runs Postgres 17: `postgresql-client-17`).
* Backups contain personal data. The folder is created readable only by you. Encrypt before copying it anywhere else
  (for example `tar c ledger-2026... | age -p > backup.tar.age`), and never commit it (`backups/` is git-ignored).
* How often: **weekly while the site is in testing, daily once real campaigns run** (a day of lost play is the
  most a table should have to redo). Example crontab line: `15 3 * * * cd /path/to/D-D-Ledger && python scripts/backup.py --out ~/ledger-backups --keep 14`
  (put the variables in a file that cron loads, not in the crontab).
* Keep at least one copy off the machine that took it.

## Prove it: the restore drill

```
docker run -d --name drill-pg -p 55432:5432 -e POSTGRES_PASSWORD=drill postgres:17
export DRILL_PG_ADMIN="postgresql://postgres:drill@localhost:55432/postgres"
python scripts/restore_drill.py ~/ledger-backups/ledger-20261014-221500Z
```

The drill never touches production. It creates a throw-away database on the Postgres server above, restores the dump
into it, drops it again, and checks:

1. the dump and every file still match the checksums taken at backup time,
2. each table has the rows the backup recorded,
3. the schema migration level is the same and every table has row-level security on,
4. every image the database points at is in the backup, and sizes match the storage ledger,
5. the real app starts on the restored data (`/healthz` green) and serves a restored image byte for byte.

It prints how long the restore took and exits non-zero on any failure. **Run it after the first backup, after any
schema change that adds a table, and at least once a quarter.** Record the result in the log at the bottom.

## Recover production

Pick the situation:

* **Bad data change or deleted campaign, project healthy (Pro+).** Dashboard -> Database -> Backups -> restore the newest
  backup before the mistake (or PITR if enabled). The project is offline while it restores. Images deleted after that
  backup are **not** restored by it: copy them back from your file backup (step 3 below).
* **Project lost or paused beyond recovery, or Free plan.** Rebuild from your own backup into a new Supabase project:
  1. Create the project; run the SQL in `supabase_storage_setup.sql` (buckets) in its SQL editor.
  2. Restore the database: `pg_restore --clean --if-exists --no-owner --no-acl --dbname "<direct connection string>" db.dump`.
     The dump keeps row-level security but not privileges (`--no-acl`), so afterwards run the SQL of
     `migrations/0002_lock_down_data_api.sql` once in the SQL editor to close the tables to Supabase's public API again.
  3. Upload the images: for every file under `files/`, upload it to bucket `ledger-uploads` under the same key
     (the folder layout is the key). The Supabase dashboard, `supabase storage cp -r files ss:///ledger-uploads --experimental`,
     or a small script using `storage.SupabaseStorage.put` all work.
  4. In Vercel set `DATABASE_URL` (transaction pooler), `SUPABASE_URL` and `SUPABASE_SECRET_KEY` to the new project and redeploy.
  5. Check `/healthz`, log in, open a campaign with images, then run `python migrate.py --status` (nothing pending).
  6. Custom database role passwords are not in backups; reset them if you use any.

## Targets (owner decision)

These are proposals for you to confirm or change:

* **Recovery point (data you can lose):** up to 24 h once real campaigns run.
* **Recovery time (how long the site may be down):** a few hours, dominated by uploading images and redeploying; the drill
  reports the database part of that time.

## Drill log

| Date | Backup | Restore time | Result | Who |
| --- | --- | --- | --- | --- |
| 2026-10-14 | Local test data (17 tables, 55 rows, 7 images), Postgres 16 | 0.1 s | PASS (9/9 checks) | Claude, in the build sandbox |
| _first production drill_ | | | _pending: needs the production connection string, which only you hold_ | |
