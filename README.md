# Campaign Ledger — D&D Campaign Manager

A browser-based app for running D&D campaigns online with your table: campaigns and
members, characters and factions, live battles, interactive maps, dice, and a 2024
rules reference. Flask + Postgres (Supabase) + Supabase Storage, deployed on Vercel.
It also runs locally.

## Features

- **Campaigns and accounts.** Self-registration, login, invitations and invite links. Each account has a basic
  profile at `/account` (display name, avatar, bio); the display name replaces the username in the sidebar, the
  username stays the login. Profiles are private to their owner for now and need no campaign. `/account` also
  changes the password (current password required; wrong guesses share the login throttle).
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

## Upload limits

Images are re-encoded before they are stored, and what is kept is counted per user and per campaign
(table `stored_files`, module `quotas.py`). Defaults: **150 MB per user**, **300 MB per campaign**; the
existing per-file caps (avatar 8 MB, sheet 25 MB, map 30 MB, import ZIP 50 MB) still apply. Change them with
`LEDGER_QUOTA_USER_MB` and `LEDGER_QUOTA_CAMPAIGN_MB` (`0` turns a limit off). The server refuses an upload
that would pass a limit, saves nothing and shows a banner; imports are checked as a whole, so a refused
import adds nothing. Deleting or replacing an image frees its space. Images uploaded before quotas existed
count as 0 bytes until you run `python scripts/backfill_storage_usage.py` once after applying migration 0021.
The campaign settings page shows the current usage.

## Backups

Supabase's own backups do not cover uploaded images (and the Free plan has none at all), so the repo ships its
own: `python scripts/backup.py` saves the database and every image, and `python scripts/restore_drill.py <backup>`
proves the backup restores into a throw-away database and that the app runs on it. Full routine, recovery steps,
targets and the drill log: [BACKUPS.md](BACKUPS.md).

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
- **JSON error shape.** Every AJAX/JSON route (anything under `/api/`, JSON requests, `/uploads/sign`)
  answers failures as `{"error": "<message for people>", "code": "<stable slug>", "request_id": "..."}`
  with the matching HTTP status (401 also carries `login_url`; quota errors carry `scope`). Build
  them with `api_error()` in `app.py`. In the browser `static/js/api.js` (`LedgerApi`) turns them into a toast.
- **Error tracking (optional).** Set `SENTRY_DSN` and add `sentry-sdk` to
  `requirements.in` (then recompile the lock file). Without both, nothing is sent. Personal
  data is not sent.

## CSS conventions

Layout CSS uses logical properties (`margin-inline-start`, `inset-inline-end`, `border-inline-start`,
`text-align: start`) instead of left/right, so a right-to-left language (Persian, say) can be added later without
rewriting the stylesheet. A deliberate physical `left`/`right` (centering with `translate`, map canvas coordinates)
carries a `/* phys */` marker; `tests/test_css_logical.py` fails on any unmarked one. Translating the interface is a
separate decision (only worth it if players read Persian).

## Tests

```
LEDGER_TEST_BACKEND=postgres pytest tests
```

The suite creates a throw-away database and a fake Supabase Storage server, so it needs no
internet or credentials. GitHub Actions runs it on every push and pull request.
`tests/test_observability.py` needs no database
(`pytest --noconftest tests/test_observability.py`).

A second workflow (`.github/workflows/srd-contract.yml`) runs nightly and fetches one spell, monster, equipment
item and magic item from the real 2024 5e API through `scripts/check_srd_contract.py`. If dnd5eapi.co renames or
drops a field the job fails and lists what changed (the normal tests fake the API and would not notice). Run it by
hand with `python scripts/check_srd_contract.py`, or from the Actions tab (Run workflow).

## Project layout

| Path | Purpose |
| --- | --- |
| `app.py` | Flask app: routes, auth, authorization |
| `database.py`, `migrations/`, `migrate.py` | Postgres access and versioned schema |
| `storage.py`, `images.py`, `quotas.py` | Object storage, image validation/optimization, upload quotas |
| `security.py` | CSRF, rate limiting, sanitization helpers |
| `observability.py` | Structured logs, request ids, error handling, optional Sentry |
| `THIRD_PARTY.md` | Audit of third-party requests, tracking, browser storage and bundled-asset licences |
| `srd.py`, `dnd5e.py` | 2024 rules reference and character rules data |
| `realtime.py`, `fog.py` | Live updates and map fog |
| `templates/`, `static/` | Jinja templates, CSS, vanilla JS |
| `scripts/` | Live check, temp cleanup, image optimization, backup and restore drill, storage-usage backfill |
| `tests/` | Pytest suite (including Playwright browser tests) |
