# Campaign Ledger — D&D Campaign Manager

A browser-based app for running D&D campaigns online with your table: campaigns and
members, characters and factions, live battles, interactive maps, dice, and a 2024
rules reference. Flask + Postgres (Supabase) + Supabase Storage, deployed on Vercel.
It also runs locally.

## Features

- **Campaigns and accounts.** Self-registration, login, invitations and invite links. Each account has a basic
  profile at `/account` (display name, avatar, bio); the display name replaces the username in the sidebar, the
  username stays the login. Profiles are private to their owner for now and need no campaign. `/account` also
  changes the password (current password required; wrong guesses share the login throttle) and offers "sign out
  everywhere else"; both bump `users.session_version`, which ends every other session on its next request. An optional
  email address is confirmed with a signed 24-hour link sent over SMTP (`mailer.py`, any provider; settings in
  `.env.example`). A forgotten password is reset from the login page with a one-hour, single-use link sent to a
  *confirmed* address (an address no account has confirmed is told so on the page, and the mail throttles limit how fast addresses can be tried); a reset ends every session and
  mails the owner a notice. The username can be changed from `/account` (current password required; you stay signed in and a confirmed email is told). Accounts can be deleted from `/account` (password + typed DELETE): campaigns the
  user solely owns are deleted for good unless handed to a member first, and the page says so next to each one. "Download my data" on `/account` returns your account, memberships and
  the characters, factions, maps and dice rolls you made as one JSON file (never the password).
  Roles per campaign: DM (full control) and Player. Authorization is enforced on the
  server for every campaign route.
- **Characters and factions.** Profiles with class, abilities, saves, skills, speed,
  avatars, sheet images, notes (individual notes can be hidden from Players), and
  factions (many-to-many). NPC stats are hidden from Players.
  A character can keep **saved attacks** (`migrations/0032_character_attacks.sql`, up to 12): name, to-hit bonus, damage dice (e.g. `1d8+3`) and damage type, added on the edit form. The sheet shows a **To hit** and a **Damage** button for each; both roll on the server like any other roll and show to everyone in the campaign (damage never uses advantage). Attacks travel with import/export, not with campaign copies. The same Attacks list (damage type under the name, To hit and Damage buttons) shows in the character popup that opens from battle rows and map pins. Card note previews now keep a line break where the editor started a new line.
  **Hide NPCs from Players** (`migrations/0033_hide_npcs.sql`): the DM or an owner switches it on in the campaign's Settings page. While on, a Player gets no NPC from the character list, faction pages, battle rows and log lines, map tokens, dice log, activity feed or the character APIs (the server filters; the page just shows what it is sent). An NPC a Player made themselves stays visible to them, the DM sees everything, and "View as player" shows exactly what a Player gets.
  The sidebar groups its links (Table, Encounters, Lore, Campaign), opens with a campaign card (name, your role) and has a **Settings** link inside every campaign; on a phone Settings is in the More sheet.
  Each character keeps its stats **level by level** (`migrations/0031_character_levels.sql`): max HP, AC, the six scores, speed and the
  saving-throw and skill proficiencies are saved for the level you save the form at. On the edit form, changing Level brings back the stats saved for that level (an
  unsaved edit survives going back and forth); a level with nothing saved starts from the current numbers. The character's own
  columns are always the active level's stats, so battle, the sheet and exports keep working unchanged, and the details page
  lists the history once more than one level is saved. Not carried: import/export bundles and campaign copies hold only the
  active level.
- **Battle tracker.** Initiative, turn tracker, HP/temp HP, conditions (including
  custom ones), group colors, live sync between players. A battle log strip sits at the bottom-left of the battle page (it follows the content when the sidebar opens or closes; the closed strip shows the newest entry in its colour, and a short wash when something new lands). Opened, a small card shows the last six lines, newest at the bottom, with names, verbs and numbers styled by kind; it records damage, healing, temp HP, conditions, down / died / revived,
  rounds and arrivals, keeps the latest 50 entries per campaign, is wiped when the battle
  is cleared, and shows Players enemy events without numbers (`migrations/0026_battle_log.sql`).
  **Death saving throws** (2024 rules): a player character at 0 HP gets a "Death saves" line under its row with three pass
  and three fail pips and a "Roll death save" button. The server rolls the d20: 10+ is a success, 9 or less a failure, a
  natural 1 counts as two failures, a natural 20 puts the character back on 1 HP; three successes make it stable, three
  failures kill it. The DM can roll for any player character, a Player only for a character they made; monsters and NPCs
  never roll. Only the DM and the owner see the tally (everyone sees "Stable"). The DM's log shows every roll, Players see
  only the outcomes (stable, back on 1 HP, died). Healing, setting HP above 0 or reviving clears the tally
  (`migrations/0027_death_saves.sql`). Not included: damage taken at 0 HP does not add a failure, and there is no
  instant-death rule for massive damage.
- **Map editor.** Shapes, pins, grid setup, zoom/pan, fog of war, DM-only layers,
  token ownership (DMs assign tokens to players), familiars that sync into the
  battle, link-to-battle.
- **Dice.** Dice page and a quick-roll widget on every page, with a shared roll feed. The die drawings (shared with the corner widget) keep their numbers readable over the facet lines. Roll toasts leave after about five seconds, also in a tab that was in the background (each toast has its own deadline, swept when the tab is shown again).
- **Activity.** `/campaigns/<id>/activity` lists the latest 200 events (members joining or leaving, characters,
  factions and maps added or removed). The actor is stored as a user id, so a deleted account shows as "a former
  member". Table `campaign_activity` (migration 0025).
- **Reference.** Spells, monsters, items and conditions from the 2024 rules (dnd5eapi.co). Hovering (or focusing/tapping) a condition chip on the battle page, or a tile in the condition picker, shows its rule.
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

## Request limits

Besides the sign-in, mail and member-search throttles, signed-in changes are limited per account (counted in the
same `login_attempts` table by `security.hit`): **240 changes a minute**, **60 dice rolls a minute**, **5 new
campaigns an hour** and **25 owned campaigns** in total. A limited request gets `429` with `Retry-After`; reads are
never limited and signing out always works. Override with `WRITE_LIMIT_USER`, `DICE_LIMIT_USER`,
`CAMPAIGN_CREATE_LIMIT` and `MAX_OWNED_CAMPAIGNS`.

`/robots.txt` lets search engines see only the sign-in pages, and every page behind sign-in carries
`X-Robots-Tag: noindex`. `/.well-known/security.txt` (RFC 9116) is published once you set `SECURITY_CONTACT` to a
`mailto:` or `https://` address; until then it returns 404.

Security headers round 2 (DL-140): the Content-Security-Policy no longer allows inline `<style>` blocks (`style-src 'self'`; `style="..."`
attributes stay allowed through `style-src-attr`), responses carry `Cross-Origin-Resource-Policy: same-origin`, and the policy has
`report-uri /csp-report`: a browser that blocks something posts there and the app logs one `csp violation: ...` warning line (nothing is
stored; the endpoint needs no sign-in). HSTS `preload` waits for the final domain (DL-134), and inline style attributes are a later step.

## Database housekeeping

Foreign keys that child tables are looked up by carry covering indexes (`migrations/0028_fk_indexes.sql`, from the Supabase
performance advisor). Tables that only grow are bounded without a cron job: `login_attempts` (24 h), `campaign_activity`
(200 per campaign), `dice_rolls` and `battle_log` (per campaign) and `notifications` (200 per user, pruned when a new one
arrives). The advisor's "RLS enabled, no policy" notices are intentional: every table is deny-all and the server uses the
service role, so do not add policies to "fix" them.

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

Native form controls are themed in one block at the end of `style.css` (DL-154): checkboxes, radios and range sliders are
drawn in brass (light and dark theme) instead of browser blue, with a visible empty-box border (3:1 or better), press
feedback, and larger targets on touch screens. Controls that already have their own look (toggle switch, access cards,
faction chips, skill tri-state, saving-throw pip, dice roll-mode) are excluded in that block's selectors; a new custom
control that hides its `<input>` needs adding to the same `:not(:is(...))` list.

Each campaign opens on a Home page (DL-144, `campaign_home`): the campaign image and bio, count tiles, the player list (DM or Player), the party, the running battle, the latest dice rolls
and recent activity. It reuses the Battle, dice and Activity queries, so NPC hiding and enemy redaction apply to it unchanged. A
"next session" card is left for DL-152.

The campaign bio is stored in the same format as character notes (a JSON list of sanitised bold/italic/bullet HTML boxes in
`campaigns.description`; old plain-text bios still read fine) and the campaign notes in `campaigns.notes` (migration 0034). Long text on
Home folds behind "Read more" (`static/js/home.js`). A character can be attached to a member (`characters.player_id`, set by the DM or an
owner from Settings > Members): `owns_character()` then lets that member edit it, read its hidden notes and make death saves, and map
tokens of that character are theirs to move (`_may_move_pin`). Deleting stays with the creator and the owners.

Print sheet (character page, DL-157): a button that opens the browser's print window, where "Save as PDF" also works. A print stylesheet
leaves out the menus, buttons, dice, roll controls and the sheet viewer, drops the cards onto white, and the page switches to the light theme
for the print only. A character whose stats are hidden from the viewer has no button.

Quests (DL-151, migration 0038): a card on the campaign Home page. The DM adds a quest (title, what the players know, optionally hidden),
marks it done or reopens it, hides it from or shows it to Players, or deletes it; Players read the quests that are not hidden. Up to 60
per campaign. Adding or finishing a visible quest shows up in the activity feed. Not built: objectives inside a quest, quest export.

Session scheduling (DL-152, migration 0035): the DM or an owner sets one "next session" per campaign from Home (`campaigns.next_session_at`
in UTC plus a short note). The browser shows it in each viewer's own time zone (`static/js/home.js`); members answer Going / Maybe /
Can't (`session_rsvps`); a new time clears the answers and sends members a notification and an activity entry; `/session.ics` gives a
calendar file. A session more than six hours past is treated as none. A timed reminder is not built (needs a scheduler).

Roll Initiative (battle page) opens a small menu on hover or focus (a tap on a phone): everyone, the player characters or the NPCs and
monsters. `POST /api/battle/roll-initiative` takes `{"scope": "all" | "pc" | "npc"}`; the others keep their numbers.

Long Rest (battle page, DM only; DL-149): every player character goes to full HP, loses temporary HP, and has death saves cleared and
is_dead reset; conditions, NPCs and monsters are left alone, and the battle log gets one line. `POST /api/battle/long-rest` takes an
optional `{"id": <participant id>}` to rest one character. Short rest and hit dice are not built (the sheet has no hit dice yet).

Visual round 3 (DL-155): a campaign without an image gets a generated cover (a hue from its id, with its initials);
a character without a portrait shows its class art, else its initial; character cards show a live HP bar and conditions for
anyone in the running battle (`_battle_live_status`, built on `_battle_rows`, so a Player gets no monster numbers); empty states sit in a brass medallion.

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
| `mailer.py` | Provider-neutral outgoing email over SMTP |
| `security.py` | CSRF, rate limiting, sanitization helpers |
| `observability.py` | Structured logs, request ids, error handling, optional Sentry |
| `THIRD_PARTY.md` | Audit of third-party requests, tracking, browser storage and bundled-asset licences |
| `srd.py`, `dnd5e.py` | 2024 rules reference and character rules data |
| `realtime.py`, `fog.py` | Live updates and map fog |
| `templates/`, `static/` | Jinja templates, CSS, vanilla JS |
| `scripts/` | Live check, temp cleanup, image optimization, backup and restore drill, storage-usage backfill |
| `tests/` | Pytest suite (including Playwright browser tests) |

## Colour contrast

Both themes pass an axe-core `color-contrast` audit (WCAG AA, 0 violations across the main pages). Convention in `static/css/style.css`: use the `-ink` tokens (`--brass-ink`, `--moss-ink`, `--blood-ink`) for text, never the raw fill colours, and `--on-brass` for text on a solid `--brass` fill.

### NPC allies
An NPC can be marked **Ally** on the character form (the switch is greyed out until NPC is on). An ally is still an NPC (NPC tag, DM-only dice, NPC initiative scope), but Players see its stats and it stays visible, in the roster, battle and on maps, when "Hide NPCs" is on. Migration `0036_npc_ally.sql`.

A monster from the manual (New character, or the Battle "Add monster" menu) also fills the Attacks card: every action with a to-hit bonus becomes an attack with its first damage roll and type. Extra riders (e.g. "+1d4 with advantage") stay in the stat-block note, and dice the card cannot hold are listed without a damage roll.

### Map ruler (DL-153, first slice)
Each map has a **Feet per square** setting (grid settings, default 5, 1-100; migration `0037_map_feet_per_square.sql`). The **Ruler** tool (next to Reset Zoom, works for Players on a locked map too) measures a drag in squares and feet, counting a diagonal step as one square like 5e does, and reads the result out in a live region. The line is local to each person and is cleared by Esc or another tool. Still to do for DL-153: sharing the ruler live and the fading cone/sphere/line/cube templates.

### Sign-up, login and Google (DL-158)
Sign-up needs a username, an **email** and a password. The email is stored unconfirmed and a confirmation link is mailed when mail is set up (it never blocks sign-up; accounts made before this rule get a banner until they add one). The login box takes a **username or an email**; if several accounts typed the same unconfirmed address and none confirmed it, email login is refused for it (the username still works). Pages: `templates/auth_base.html`, `static/css/auth.css`, `static/js/auth.js`.

**Sign in with Google** is an OAuth code flow with PKCE and a state check (`/auth/google`, `/auth/google/callback`). It stays hidden until `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set; set `GOOGLE_REDIRECT_URI` only if the callback URL cannot be worked out from the request. In Google Cloud Console create a Web application OAuth client and add `https://<domain>/auth/google/callback` as an authorized redirect URI. Google must report a verified email. The same confirmed email links to the existing account; an unconfirmed address typed on another account is never linked and is cleared. A Google account has no password until the person adds one under Account (renaming and deleting work without one).

### Profiles (DL-97, first step; migration `0039_profiles_and_google.sql`)
`/u/<username>` shows avatar, name, bio, member-since and up to 6 featured player characters (name, class, level, portrait only; never the campaign, stats or notes). Under Account the owner picks who can open it: everyone signed in, people who share a campaign (default), or only themselves. A profile you may not open answers 404, the same as a name that does not exist. Campaign Home links player names to their profiles unless private.
