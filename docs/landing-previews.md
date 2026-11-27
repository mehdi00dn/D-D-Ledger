# Landing page previews (DL-186 to DL-190)

The empty image frames on the home page (`templates/landing.html`) are filled with small working scenes from the product instead of screenshots,
one section per task. They are composed for the page, not copied from the app: the painted world behind, paper cards from the app's own material
(`.o-paper`, `oaken.css`) laid over it, and the tutorial campaign's real people and text, so a visitor sees the people they will meet.
Everything is demo data: nothing is saved, sent or read from the server, and a visitor can try the controls without an account.

## Files

- `static/css/landing-previews.css`: loaded after `landing.css`. Every class starts with `lp-`.
- `static/js/landing-previews.js`: deferred. Pieces are found by `data-lp="name"` and set up one at a time inside a `try`, so one that breaks never stops the others.
- `static/assets/landing/*.webp`: the tutorial party's portraits (the tutorial export's avatars, resized to 144px webp).
- The tutorial party and its text (`party`) and a class icon macro (`class_icon`) sit at the top of `landing.html`.
- `tests/test_landing.py`: a frame is either an empty hidden slot or a named group; each preview has its real controls; every image, stylesheet and script is served.

## A preview

`<figure class="shot lp" data-slot="IMG-0x" data-lp="name" role="group" aria-label="...">`. The figure keeps its `data-slot` id from the brief and is the
container for container queries (the frame is measured, not the window), so the scene sits in a child element (`.lp-scene`): a container query styles
descendants, never the container itself.

## Rules every preview follows

- No inline styles (the Content-Security-Policy forbids them): the first state is carried by markup and classes; changes while you play go through the style object (`--p`, a token's place).
- Motion is transform and opacity only, 150 to 480 ms, ease-out, and off under `prefers-reduced-motion`.
- The entrance (pieces rise in once) is armed by the script only for a frame that starts below the fold, and released when it scrolls into view. A frame already in view, no script, or reduced motion: everything is simply there. Nothing can stay hidden.
- Controls are real buttons, checkboxes and radios with visible focus. Text on paper keeps 4.5:1 or better. A scene never changes height while you play (stacked cards share one grid cell).
- Without the script a preview still shows its first state.
- Framing and edges: a scene gets a rounder frame (20px) than a plain screenshot (12px, `landing.css`), with a hairline, a light top edge and layered shadows. Anything inset from the frame is concentric with it (the brass inlay line is the frame radius minus the border minus its inset), paper cards get a hairline and the light catching their top edge, and portraits get a hairline rim so their edge never looks cut out.
- Changing what a card shows is a CSS transition, never a keyframe, so it can be interrupted and retargeted when someone clicks through quickly.

## 01, Keep the story organized (DL-186)

IMG-02 is the campaign scene: the painted valley behind a "Tutorial" title (Active, 6 characters, 1 faction, 1 map), the party as a column of round portraits
(a radio group: click, or the arrow keys, Home and End), the picked hero's card (portrait in a ring, class icon, level, HP, AC, and their line from the campaign notes),
and the mission on paper over it: three steps from the quest text (investigate the camp, face the goblins, recover the stolen goods). Tick them all and a
"Quest complete" stamp lands. On a phone the party becomes a row above the cards.
