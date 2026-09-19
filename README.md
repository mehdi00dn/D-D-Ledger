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
- `uploads/avatars/`, `uploads/maps/` and `uploads/sheets/` — uploaded images
  are stored as files locally. On Vercel they use temporary function storage,
  so they can disappear or be unavailable from another instance. Postgres
  stores the references, not the image bytes.

## What's included

- **Characters** — full profiles with level, HP, AC, the six ability scores,
  an avatar (with a drag/zoom crop tool and one-click removal), multiple
  attached sheet images, freeform notes, and an optional group assignment.
- **Groups** — simple factions/parties with a large avatar image, bio, and a
  color that doubles as their side-marker in battle.
- **Battle** — bring characters onto the field (search for existing ones,
  add an entire group at once, or create a new one on the fly), track
  initiative and AC live (AC edits here are a temporary in-battle override —
  a character's base AC on their profile is never touched), heal/damage
  with one click, grant temporary HP (which absorbs damage before real HP,
  per the rules), mark deaths (with an undo), remove combatants (with a
  yes/no confirmation), and sort the field by initiative or by group. Each
  combatant's avatar is bordered in their group's color. The HP bar itself
  is also a scrubber — click or drag across it to set HP directly.
- **Maps** — import a map image, or start from a blank white grid canvas
  at whatever size you like (adjustable any time from the toolbar — this
  rescales existing drawings and pins to fit). Draw lines, rectangles, and
  ovals (hold Shift to draw from the center, Alt to lock to a 1:1 ratio —
  both work together), or freehand with the pen tool; toggle a fill with
  its own opacity slider. Drag the grid to reposition it and adjust its
  size independently. Drop familiar/marker pins (paw, skull, sword) that
  scale to match the grid. Check "Link to current battle" to auto-populate
  character pins from your battle roster, sized to the grid and bordered in
  their group's color — drag them into position, click a pin for a resize
  handle (drag to scale it up for large creatures) and a details button
  (opens the same character viewer as the Battle screen). Downed characters
  automatically grey out on the map the moment they die in battle.
- **Sheet image viewer** — click any sheet image to open a full-size
  preview. Click anywhere on it to zoom toward that spot; while zoomed,
  hold and drag to pan around; click again to zoom back out. A delete
  button in the preview removes the image from the database and disk.
- **Dice** — tap to queue any mix of d4/d6/d8/d10/d12/d20/d100, roll them
  all at once with an animated flip (d6 results show real pip dots), and
  see the auto-totaled result (plus an optional flat modifier).
- **Import / Export** — export your whole campaign, or just a single
  character or group, as a `.zip` with images included. Importing matches
  groups by name (so re-importing won't duplicate a faction) while always
  adding characters as new entries (so importing never silently overwrites
  anything — clean up duplicates manually if you import the same file
  twice).
- **Automatic image optimization** — any upload over 1MB is automatically
  downscaled and recompressed on save, so large phone photos don't bloat
  your database folder.
- **Light / dark mode** — toggle in the bottom of the sidebar; your choice
  is remembered across sessions.

## Customizing the look and feel

Everything is plain HTML/CSS/JS, so it's fully editable by hand:

- **`static/css/style.css`** — the entire visual design. All colors are CSS
  custom properties defined at the top of the file (`:root { --brass: ...;
  --blood: ...; }` etc.), with a second block right below for light mode
  (`:root[data-theme="light"] { ... }`). Change a variable once and it
  updates everywhere that color is used.
- **`templates/_icons.html`** — every icon in the app is inline SVG defined
  in one Jinja macro here. To change an icon, find its `elif name ==
  '...'` block and edit the SVG path, or add a new one and reference it as
  `{{ icon('your-name') }}` anywhere in the templates.
- **`templates/*.html`** — one file per page/section (`characters_list.html`,
  `battle.html`, `dice.html`, etc.), plus `base.html` for the shared shell
  (sidebar, modals) that every page extends.
- **`static/js/*.js`** — one file per feature area (`battle.js`, `dice.js`,
  `cropper.js`, `lightbox.js`, `theme.js`, `confirm.js`). Each is
  independent and commented.

No build step — just edit and refresh your browser.

## Vercel deployment

See [VERCEL_DEPLOY.md](VERCEL_DEPLOY.md) for Supabase setup, environment
variables, migrations, region selection, and the current upload-storage
limitation. Never commit `.env` or production credentials. The GitHub Actions
workflow runs the Postgres-backed test suite for pushes and pull requests.
