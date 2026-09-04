# Campaign Ledger — D&D Battle Manager

A local desktop-style app for managing your D&D campaign: characters, groups,
live battles, and a dice roller. Runs entirely on your machine and opens in
your browser — no internet required after setup (aside from loading fonts).

## Setup (one-time)

You need Python 3.9+ installed. Then, from this folder, run:

```
pip install -r requirements.txt
```

## Running the app

```
python app.py
```

Then open **http://127.0.0.1:5000** in your browser. Leave the terminal
window open while you use the app — closing it stops the server.

To stop the server, go back to the terminal and press `Ctrl+C`.

## Where your data lives

- `dnd.db` — a SQLite database file that's created automatically the first
  time you run the app. This holds all your characters, groups, and battle
  state. **Back this file up** if you want to keep your campaign data safe —
  just copy it elsewhere, or use the in-app Export feature.
- `uploads/avatars/` and `uploads/sheets/` — every image you upload (avatars,
  character sheets) is stored here as a plain file, referenced by the
  database. If you back up `dnd.db` manually, back up this folder too.

Deleting `dnd.db` (with the app stopped) gives you a completely fresh start.

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

## A note on scale

This was built for a single DM's use on their own machine — the database is
a single file, there's no login system, and only one battle is tracked at a
time (starting a new one clears the last). That's intentional for how it's
meant to be used, but worth knowing if you ever want to extend it.
