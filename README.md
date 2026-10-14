# Campaign Ledger — D&D Campaign Manager

A browser-based app for running D&D campaigns online with your table: campaigns and
members, characters and factions, live battles, interactive maps, dice, and a 2024
rules reference. Flask + Postgres (Supabase) + Supabase Storage, deployed on Vercel.
It also runs locally.

## Features

- **Campaigns and accounts.** Self-registration, login, invitations and invite links.
  Roles per campaign: DM (full control) and Player. Authorization is enforced on the
  server for every campaign route.
- **Characters and factions.** Profiles with class, abilities, saves, skills, speed,
  avatars, sheet images, notes (individual notes can be hidden from Players), and
  factions (many-to-many). NPC stats are hidden from Players.
- **Battle tracker.** Initiative, turn tracker, HP/temp HP, conditions (including
  custom ones), group colors, live sync between players.
- **Map editor.** Shapes, pins, grid setup, zoom/pan, fog of war, DM-only layers,
  token ownership (DMs assign tokens to players), familiars that sync into the
  battle, link-to-battle.
- **Dice.** Dice page and a quick-roll widget on every page, with a shared roll feed.
- **Reference.** Spells, monsters and items from the 2024 rules (dnd5eapi.co).
- **Import / export** of campaigns as ZIP bundles.

## Running locally

Python 3.12 and a Postgres database are needed.

```
pip install -r requirements-dev.txt
cp .env.example .env              # set DATABASE_URL and SECRET_KEY
docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ledger postgres:16
python app.py                     # AUTO_MIGRATE=1 creates the tables
```

Open **http://127.0.0.1:5000**. Uploads go to `./uploads` locally (no Supabase needed).

## Where data lives

- **Postgres** holds characters, groups, maps, battle state and accounts. Schema changes
  are versioned in `migrations/` and applied with `python migrate.py`.
- **Uploaded images** are validated, re-encoded and stored in object storage: Supabase
  Storage in the cloud (private buckets), or `uploads/` in development. The database keeps
  only the file keys, and images are served through the app behind login.

## Deployment

See [VERCEL_DEPLOY.md](VERCEL_DEPLOY.md): Supabase setup, Vercel environment variables,
verification, rollback and operations. Never commit `.env` or production credentials.

## Monitoring and troubleshooting

- **`/healthz`** (public) reports whether the database, schema and storage are ready
  (HTTP 200, or 503 with a short hint). It never exposes connection strings.
- **Logs.** On Vercel the app writes one JSON object per line (`LOG_FORMAT=text` for plain
  lines locally, `LOG_LEVEL` to change verbosity). Every request has an `X-Request-ID`
  (also in the response headers) and one access line with method, route, status, duration
  and user id. Query strings are never logged.
- **Errors.** An unexpected server error shows the user a generic message and a reference
  (the request id). Search the logs for that id to find the stack trace.
- **Error tracking (optional).** Set `SENTRY_DSN` and add `sentry-sdk` to
  `requirements.in` (then recompile the lock file). Without both, nothing is sent. Personal
  data is not sent.

## Tests

```
LEDGER_TEST_BACKEND=postgres pytest tests
```

The suite creates a throw-away database and a fake Supabase Storage server, so it needs no
internet or credentials. GitHub Actions runs it on every push and pull request.
`tests/test_observability.py` needs no database
(`pytest --noconftest tests/test_observability.py`).

## Project layout

| Path | Purpose |
| --- | --- |
| `app.py` | Flask app: routes, auth, authorization |
| `database.py`, `migrations/`, `migrate.py` | Postgres access and versioned schema |
| `storage.py`, `images.py` | Object storage and image validation/optimization |
| `security.py` | CSRF, rate limiting, sanitization helpers |
| `observability.py` | Structured logs, request ids, error handling, optional Sentry |
| `srd.py`, `dnd5e.py` | 2024 rules reference and character rules data |
| `realtime.py`, `fog.py` | Live updates and map fog |
| `templates/`, `static/` | Jinja templates, CSS, vanilla JS |
| `scripts/` | Live check, temp cleanup, image optimization |
| `tests/` | Pytest suite (including Playwright browser tests) |
