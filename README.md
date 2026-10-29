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
Rest (battle page, DM only): one dropdown button holds Short rest and Long rest. Short rest: a dialog lists each living player character; the DM types how many HP each regains (capped at max HP), death saves are cleared, and one line goes in the battle log. The battle menu no longer has a Create Character button.
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

### Account settings sections (DL-39)
`/account` is split into five sections with a side menu (a row of chips on a phone): **Profile**, **Privacy** (who can open your profile page, featured characters), **Sign-in and security** (email, username, password, sign out everywhere), **Appearance** (dark, light or match the device; saved in this browser under `ledger-theme`, value `system` is read by the head script of every page) and **Your data** (download, delete). Without JavaScript every section shows. A link such as `/account#email-panel`, an error banner or a `?mail=` result opens the section it belongs to; otherwise the last section is remembered for the tab. Notification settings are not part of this yet because there are no notification preferences to set. Script: `static/js/account.js`.

### Party treasury (DL-148; migration `0040_treasury.sql`)
The Treasury page (`/campaigns/<id>/treasury`, sidebar under Table) is the party purse. The purse is not stored: it is the sum of the entries in `treasury_entries`, each holding a signed change per coin (cp, sp, ep, gp, pp), who made it, an optional character it was for, and a note. Any member can add coins, spend coins, or split coins; the DM can remove any entry and a member their own. Spending pays the coins named first, makes up any shortfall from the smallest coins, and gives change back (pp, gp, sp, cp), so the purse never goes below zero in any coin; removing an entry is refused when later entries already spent its coins. Values are worked in copper (cp 1, sp 10, ep 50, gp 100, pp 1000). "Split evenly" shares typed coins, or the whole purse when none are typed, between the chosen player characters: each gets the same share rounded down, the rest stays in the purse, and it is recorded as one spending entry. Entries show in the campaign Activity feed. Not built: links to the item manual (DL-114) and inventory (DL-119).

### Treasury follow-up: character purses (DL-148; migration `0043_treasury_purses.sql`)
Every player character now has a purse of its own next to the party purse. An entry belongs to exactly one purse: `treasury_entries.character_id` NULL is the party's, a character id is that character's (entries that already named a character now count in that character's purse). The Treasury page shows the party purse, a "Character purses" list (coins, total, links to the profile and to that purse's history), the **Add or spend** form (choose whose purse) and **Share or hand over** (take coins from one purse and split them evenly between any purses, or hand them to one; no amount means the whole purse; what does not divide stays behind). The entries of one share carry the same `batch` and are removed together. Anyone may use the party purse; a character's purse is for the DM and whoever made or plays the character. Each player character's profile has a Purse section (coins, last entries, links). Deleting a character returns its coins to the party purse. The page opens with a short "How the treasury works" guide. Coins show as a small coin icon in each coin's colour; mistakes (no recipient ticked, no amount) are explained on the page instead of a bare error.


### Treasury page flow (DL-148 follow-up)

The purse figure in gold (for example "worth 190 gp") counts every coin in that purse as gold (1 pp = 10 gp, 1 ep = 0.5 gp, 1 sp = 0.1 gp, 1 cp = 0.01 gp), so 40 gp and 15 pp show as 190 gp; the coin chips beside it are the raw coins. The top line shows the total of every purse. **Add or spend** and **Share** are dialogs, opened from the page header or from the button on a purse (which pre-selects that purse); errors show inside the dialog. Entries show 10 to a page, newest first, with Newer/Older and page numbers (`?page=`, filter `?who=`). Everything updates in place: `static/js/treasury.js` posts the forms with `X-Requested-With: fetch` (the server answers JSON `{ok, message}` or `{error}` instead of a redirect), then fetches `/campaigns/<id>/treasury/panels` (the purses, entries and dialogs as an HTML fragment) and swaps it in; filter chips and page links do the same and keep the address bar in step. Without the script every form and link still works as a normal post and page load. A new entry sends you back to page one of "Everyone" so it shows on top; removing an entry keeps your page. Opening the page with `?holder=<character id>` (the character page's Add or spend link) opens the entry dialog for that purse.
### Encounter follow-up (DL-147; migration `0044_encounter_characters.sql`)
The Encounters list names each encounter in an editable field (rename in place, Enter saves, Esc cancels; `POST .../encounters/<id>/rename`) and has **Run in battle** (Average or Rolled HP) on every card. Saving in the builder closes it and returns to the list.

One character per monster: running an encounter (or adding from the monster manual) puts all copies in the battle but keeps a single shared character in the Characters list ("Goblin Warrior"; the battle numbers the copies #1, #2...). That character is marked "Added from encounter" (or "monster manual"; `characters.added_from`). `added_sig` is a fingerprint of the character as it was made (stats, notes, pictures, group, attacks), so a character you edited is noticed and left alone: a fresh one is made instead, and it is never removed automatically. Rolled hit points that differ from the average get a character per total, named like "Goblin Warrior (12 HP)".

Deleting an encounter asks whether its creatures should leave the character list too: "Delete encounter only", or "Delete and remove its creatures" (only unedited ones that are not in the battle). The new-encounter and name fields have a clearer design, and both pages open with a short guide.

### Your characters (DL-160; migrations `0045_character_library.sql`, `0046_your_characters_wording.sql`)

A person's own characters, kept outside any campaign (`/library`, shown as "Your characters" in the navigation outside a campaign; it is deliberately not in a campaign's sidebar, because the campaign Characters page reaches it directly). A kept character is an ordinary `characters` row with `campaign_id` NULL and `owner_id` set (a check constraint says a character has a campaign or an owner), so every campaign query ignores it. Only the owner can open, edit or delete it. The create/edit form is the campaign form with the NPC, faction and monster-manual parts left out. The URLs and code keep the older word "library".

Less going back and forth between menus:

- **Campaign Characters page**: the header has New character, "From your characters" (a dialog with search and checkboxes; "Add N to this campaign" posts to `/library/add-many` and adds one copy of each, skipping any already there), and a "more" menu with Export this campaign, Import from a file and Open Your characters.
- **New campaign character**: an "Also keep a copy in Your characters" checkbox (player characters only; it links both ways like Save does).
- **New character in Your characters**: an optional "Add to a campaign as well" select makes the character and its campaign copy in one step; opening Make a new one from the picker pre-selects that campaign.
- **Details pages**: back link above the title, Edit as the one primary button, and a "more" menu (Print, Export this character, Show on my profile, Delete). Campaign characters get "Save to your characters" / "In your characters" beside Edit.
- **Cards**: no HP pill or live HP meter any more. The meta line shows class, level and AC; a card for someone in the running battle shows "In battle" (or "Down") and condition chips, and a Player still gets nothing for a monster.

Behaviour:

- **Add to a campaign** (`/library/<id>/add-to-campaign`, from a card, the details page or the picker): puts a copy in, played by the person, with its own copies of the portrait and sheet images, level history and attacks. `characters.library_id` on the copy points back at the kept character, so one campaign never gets the same character twice; changes at one table never touch the kept sheet or other tables. The copy shows "Added from their own characters".
- **Save to your characters** (`/library/save`, from the details page of a player character you made or play): the reverse copy, linked both ways round; saving again opens the one already kept.
- **Export and import** (`/library/export`, `/library/<id>/export`, `/library/import`): the same zip format as a campaign export (manifest.json version 1 plus images), downloaded as `your_characters.zip`. Importing works both ways: a campaign export can be imported into Your characters and the reverse. Into Your characters only player characters are kept (NPCs and factions are skipped and counted in the banner), the limit still applies, and image storage quota is checked before anything is written.
- **Profile**: a star (in the card's menu or on the details page) features a kept character in the profile showcase (same table and limit as before, `profile_showcase`); the account page picker lists them too.
- Limit of 60 characters (`LIBRARY_MAX`). Deleting one removes its image files but keeps the campaign copies; deleting the account removes them and their files; the account export lists them.

### Map ruler follow-up (DL-153)
The ruler is drawn on its own canvas above tokens, shapes and fog, in screen pixels, so it stays small and sharp at any zoom or map size. It has a thin brass line with a dark edge, a tick at every square, hollow end rings and a compact reading pill.

### Encounter builder (DL-147; migration `0041_encounters.sql`)
DM only (a list of monsters is a spoiler). The Encounters page (`/campaigns/<id>/encounters`, sidebar under Encounters) lists saved encounters with their difficulty; opening one shows the builder: search the 2024 monster manual, add monsters with +/- counts, and a meter compares the total XP with the party's budget. The budget follows the 2024 Dungeon Master's Guide table (`encounters.py`, XP per character by level for Low, Moderate and High) and is the sum over the player characters ticked in the Party box; with no player characters yet a party size and level can be typed. There is no group-size multiplier in the 2024 rules: monster XP is added at face value. The difficulty shown is the lowest budget that holds the total (Low includes anything lighter), or Over High. Saving copies each monster's name, CR and XP into `encounter_monsters`, so the list shows without the rules API. "Run in battle" saves, then creates every monster as an NPC with average or rolled hit points (same code as adding one monster from the Reference page, `_add_monsters_to_battle`) and opens the battle; the list stays and can run again. Limits: 30 lines, 30 of one monster, 40 creatures per run, 100 encounters per campaign. Not built: the durable battle/encounter entity (DL-13), grouping by faction, or saving a battle as an encounter.

### Shape attached to a token (DL-159; migration `0042_shape_attach.sql`)
A shape can be tied to a token. Select the shape, press the link button in its action bubble, then click a token; the shape stays exactly where it is (nothing is aligned). From then on, moving the token moves the shape by the same distance. Pressing the button on an attached shape detaches it, Esc cancels, and a dotted line shows the link while the shape is selected. The server shifts attached shapes in the same save as the token move (`map_drawings.attached_pin_id`), so every screen agrees; dragging, undo/redo and live movement on other screens glide the shape along. Locked and DM-only shapes follow too. Deleting a token only detaches its shapes. A Player can attach only to tokens they can see, and the id of a hidden token is never sent to them. Map export and import do not carry the attachment. A shape whose centre hides behind its token cannot be clicked, so a token with attached shapes shows a link button in its bubble: its menu lists each attached shape with Lock/Unlock, Select and Detach. Tests: `tests/test_map_attach.py`, `tests/test_e2e_map_attach.py`.

### Accessibility pass (DL-72, first slice)
An axe-core audit (WCAG 2.1 A and AA plus best practices) of 19 pages, in dark and light themes at desktop and phone width, found five kinds of issue and all are fixed: empty states and card titles were `h3` straight under the page `h1` (now `h2`); the menu `aside` and a second `aside` on the character and campaign settings forms shared the same landmark name (both are named now); the encounter builder had no `h1`; the note editors had no accessible name; and two selects (a member's role, the invite status) were named only by their `title`. The audit now finds nothing on those pages. `tests/test_a11y_structure.py` keeps the page outline honest (one `h1`, no skipped heading levels, named landmarks and controls). Not covered: the map editor canvas, and a manual screen-reader and keyboard walk-through, which stay open under DL-72 and DL-71.

Treasury dialogs show how many of each coin the chosen purse holds, and the confirm buttons are "Coins in" and "Coins out". Character cards show two lines of notes. The home banner is blurred, with a corner button that opens it full size.
Campaign images are stored up to 2000 px on the longest edge (other avatars stay at 512 px); images uploaded before this keep their old size until they are uploaded again. The Coins in / Coins out buttons are green and red. On a sideways phone the battle log sits to the right of the two round buttons at the bottom left.

### Profile setup after sign-up (DL-161; migration `0047_profile_setup.sql`)
Every new account, whether made with a password or with Google, is sent once to `/welcome`, "Set up your profile": photo, username (Google sign-ups start with an automatic one), display name, bio and who can see the profile. "Save and continue" or "Skip for now" both end it for good (`users.profile_setup_at`); the page the person was heading to is kept. Accounts that existed before the migration count as done. An account that never finished it also passes through the page once at its next sign-in. Everything on it stays editable in Account settings. Tests: `tests/test_welcome.py`, `tests/test_e2e_welcome.py`.

Uptime check (DL-139): `.github/workflows/uptime.yml` runs `scripts/check_uptime.py` every 15 minutes against the repository variable `SITE_URL`; it needs HTTP 200 and `ok: true` from `/healthz` (retried twice) and otherwise fails the run, which GitHub emails. Skipped while `SITE_URL` is unset. Test: `tests/test_check_uptime.py`.

Data inventory (DL-84): `docs/DATA_WE_COLLECT.md` lists every piece of personal data the app keeps, why, who can see it, what is deliberately not collected, and what is sent to other services. `tests/test_data_inventory.py` fails if a `users` column is added without being listed there.

Privacy page (DL-69): `/privacy` is public. It is linked under the log-in and sign-up forms, in the sidebar footer and phone More sheet on pages outside a campaign (campaigns list, account), and from Account settings. Inside a campaign the menus hold only game pages and campaign settings, so legal and account pages are never added there. It restates `docs/DATA_WE_COLLECT.md` in plain words. Set `PRIVACY_CONTACT` (an email address) in the environment to show a "Questions" line. It is a draft written from what the code does, not legal advice; have it reviewed before launch (DL-80). Test: `tests/test_privacy_page.py`.

Terms and Cookies pages (DL-74, DL-77): `/terms` and `/cookies` are public, next to `/privacy`, share the same small tab row, and are linked the same way (log-in and sign-up forms; outside campaigns only in the sidebar and More sheet). Same `PRIVACY_CONTACT` contact line. Both are drafts from what the code does, not legal advice: review with DL-80 and add business details (DL-83) before launch. Tests: `tests/test_privacy_page.py`.
