# Campaign Ledger — D&D Battle Manager

A browser-based app for managing your D&D campaign: characters, groups, live
battles, maps, and a dice roller. It runs locally or as a Vercel deployment
with Postgres.

## Setup (one-time)

You need Python 3.9+ installed. Then, from this folder, run:

```
pip install -r requirements.txt
```

## Running the app

The app stores everything in **Postgres** (`DATABASE_URL`). See
`VERCEL_DEPLOY.md` for the full setup (Supabase + Vercel, and local dev).
For a quick local run:

```
pip install -r requirements.txt
cp .env.example .env                 # edit DATABASE_URL / SECRET_KEY
python app.py                        # AUTO_MIGRATE=1 creates the tables
```

Then open **http://127.0.0.1:5000** in your browser. To stop the server press
`Ctrl+C` in the terminal.

## Where your data lives

- **Postgres** (the database `DATABASE_URL` points to) holds all your
  characters, groups, maps and battle state. Schema changes are versioned in
  `migrations/` and applied with `python migrate.py`. Back the database up with
  your provider's tools, or use the in-app Export feature.
- **Uploaded images** (avatars, character sheets, map backgrounds) are validated,
  re-encoded and stored in object storage: **Supabase Storage** in the cloud (two
  private buckets), or the local `uploads/` folder in development. The database keeps
  only the file keys. Images are served through the app and require login.

## Vercel deployment

See [VERCEL_DEPLOY.md](VERCEL_DEPLOY.md) for the full setup: Supabase (tables and
storage buckets), Vercel environment variables, region, verification (`/healthz` and
`scripts/live_check.py`), rollback, and operations. Never commit `.env` or production
credentials. The GitHub Actions workflow runs the Postgres-backed test suite for pushes and
pull requests.
