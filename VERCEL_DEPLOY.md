# Deploying Campaign Ledger (Vercel + Supabase)

Everything durable lives in Supabase, so any number of serverless instances see the same data:

| What | Where |
|---|---|
| Users, campaigns, characters, maps, battles | Supabase **Postgres** (`DATABASE_URL`) |
| Avatars, character sheets, map images | Supabase **Storage**, two private buckets |
| Nothing else | The Vercel function's disk is never used |

Images are served **through the app, behind login** (`/uploads/...`), never from a public bucket.
Large files (over ~3.4 MB after the browser shrinks them) go straight from the browser to Supabase
Storage, so they never hit Vercel's 4.5 MB request limit.

## 1. One-time Supabase setup
1. **Project.** Create it in the EU region closest to your players (Frankfurt, `eu-central-1`).
2. **Tables.** Either run from a terminal (records what is applied, safe to re-run):

       pip install -r requirements.txt
       # macOS / Linux
       DATABASE_URL="postgresql://...:6543/postgres?sslmode=require" python migrate.py
       # Windows PowerShell
       $env:DATABASE_URL = "postgresql://...:6543/postgres?sslmode=require"; python migrate.py

   or paste **`supabase_setup.sql`** into Supabase -> **SQL Editor** -> Run (once only, one
   transaction). Do not use both methods on the same database. A new database is empty: until
   the tables exist, the login page loads but every action returns **500**.
3. **Storage buckets.** Paste **`supabase_storage_setup.sql`** into the SQL Editor and run it.
   It creates `ledger-uploads` and `ledger-temp` (both private, with size/type limits).
4. **Connection string.** Project Settings -> Database -> Connection string -> **Transaction
   pooler** (port 6543) and append `?sslmode=require`.

`migrations/0002` switches on Row Level Security for every table, which blocks Supabase's public
REST API from reading them. The app connects directly as the table owner, which is unaffected.
Every new table you add must also enable RLS (a test enforces this).

## 2. Vercel environment variables
Project -> Settings -> Environment Variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | The pooler string from step 1.4. Use your **real project reference** - do not leave `[YOUR-PASSWORD]` brackets or a template such as `postgres.PROJECT_REF`. URL-encode special characters in the password (`@` becomes `%40`), or reset it to letters and digits. |
| `SECRET_KEY` | A random string of **at least 32 characters**; the app refuses to start with less. `python -c "import secrets; print(secrets.token_hex(32))"`. Changing it logs everyone out. |
| `SUPABASE_URL` | Added automatically by the Supabase integration. |
| `SUPABASE_SECRET_KEY` | Added automatically. (`SUPABASE_SERVICE_ROLE_KEY` is accepted as a fallback; Supabase is retiring those legacy keys.) |

**Production vs Preview.** Variables are per environment. If you want Preview deployments (any
branch other than the production branch) to run, tick **Preview** on `SECRET_KEY`, `SUPABASE_URL`
and `SUPABASE_SECRET_KEY` as well - otherwise a preview build refuses to start and lists what is
missing. A preview shares your one database and buckets, so use it only for short checks.

`vercel.json` pins the function region to `fra1` (matching a Frankfurt database). If your database
is elsewhere, change `regions`. A function far from its database adds latency to every query.
Do **not** add a `functions` block to `vercel.json`: Vercel does not allow `functions` together
with `builds` and the deployment would fail. Function time limits are set in the Vercel dashboard.

## 3. Deploy and verify
1. Push to the production branch (`vercel`). Vercel builds and deploys.
2. Open `https://<your-app>.vercel.app/healthz`:

   | Response | Meaning |
   |---|---|
   | `{"ok": true, "stage": "ready", ...}` | Database reachable, tables exist |
   | `stage: "schema"` (503) | Connected, tables missing -> step 1.2 |
   | `stage: "connect"` (503) | Cannot connect; the `hint` says what to check in `DATABASE_URL` |

   It never shows passwords or connection strings (details go to the Vercel logs).
3. Run the end-to-end check from **the computer your players use** (it also proves they can reach
   Supabase Storage for large uploads):

       pip install requests
       python scripts/live_check.py --base https://<your-app>.vercel.app

   It plays two users through sign-up, campaigns, characters, small and large uploads, maps,
   battle, live sync, isolation between campaigns and delete redirects, then deletes its
   campaigns. It leaves two `livecheck_*` users; remove them in Supabase with
   `delete from users where username like 'livecheck_%';`

**If a deployment misbehaves:** Vercel -> Deployments -> pick the previous good one -> **Promote
to Production** (instant rollback). Nothing in the database changes.

## 4. Operating it
* **Temp files.** Abandoned upload staging and large-export files pile up in `ledger-temp`.
  `python scripts/cleanup_temp.py --dry-run`, then without `--dry-run`, now and then
  (needs `SUPABASE_URL` + `SUPABASE_SECRET_KEY` in your shell).
* **Backups.** Supabase's free plan has none. Export from the app (Campaign -> Export) or use
  `pg_dump` with the *direct* connection string. Consider the Pro plan before real campaigns.
* **Free plan pauses** projects after a week of inactivity; the site then returns 503 from `/healthz`
  until you resume it in the Supabase dashboard.
* **Schema changes.** Add `migrations/000N_name.sql`, run `python migrate.py` from a trusted
  machine *before* deploying the code that needs it, and regenerate the paste-script:
  `python migrate.py --sql > supabase_setup.sql`. CI never touches your live database.
* **Keys.** Never paste `DATABASE_URL` or Supabase keys into chat, issues, or git. If one leaks,
  rotate it in Supabase and update Vercel, then redeploy.

## 5. Local development
    pip install -r requirements-dev.txt
    cp .env.example .env          # AUTO_MIGRATE=1 creates the tables on start
    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ledger postgres:16
    python app.py                 # images are stored in ./uploads (no Supabase needed locally)

## 6. Tests and CI
    LEDGER_TEST_BACKEND=postgres pytest tests
The suite creates and drops its own throw-away database (set `TEST_PG_ADMIN` if your local Postgres
is not the default) and starts a fake Supabase Storage server in-process, so it needs no internet
or credentials. GitHub Actions (`.github/workflows/ci.yml`) runs the same suite on every push and
pull request.
