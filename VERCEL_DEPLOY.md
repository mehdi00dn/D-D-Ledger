# Deploying Campaign Ledger (Vercel + Supabase Postgres)

State now lives in Postgres, so every serverless instance sees the same data.
(Uploaded images still use instance-local disk until Phase 2 -- expect broken
images on Vercel until then; everything else is persistent.)

## 1. Create the database (Supabase)
1. New project. **Region:** pick the EU region nearest your players (Frankfurt,
   `eu-central-1`, is the safe default for players in Iran).
2. Project Settings -> Database -> Connection string -> **Transaction pooler**
   (port 6543).  Append `?sslmode=require`.  This is your `DATABASE_URL`.
   Copy the password once; keep it out of chat and out of git.

## 2. Create the tables (once, and after every new migration)
From your machine (this records what has been applied, so future migrations
are safe to re-run):

    pip install -r requirements.txt
    DATABASE_URL="postgresql://...:6543/postgres?sslmode=require" python migrate.py
    DATABASE_URL="..." python migrate.py --status        # shows applied / PENDING

Do not paste the SQL by hand into the Supabase editor -- `migrate.py` would then
try to apply `0001` again.

## 3. Configure Vercel
Project -> Settings -> Environment Variables (Production + Preview):
* `DATABASE_URL`  the pooler string from step 1
* `SECRET_KEY`    `python -c "import secrets; print(secrets.token_hex(32))"`

Project -> Settings -> Functions -> **Function Region**: set it to the region
closest to the database (Frankfurt `fra1` for a Frankfurt database).  A function
far from its database adds latency to every query.

Redeploy.  Nothing runs at cold start except importing the app.

## 4. Local development
    pip install -r requirements-dev.txt
    cp .env.example .env         # then edit; AUTO_MIGRATE=1 applies migrations on start
    docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ledger postgres:16
    python app.py                # (or use a second, free Supabase project as your dev DB)

## 5. Tests
    LEDGER_TEST_BACKEND=postgres pytest tests
The suite creates and drops its own throw-away database; set `TEST_PG_ADMIN`
(libpq string) if your local Postgres is not the default one.
