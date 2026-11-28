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

## 01, Keep the story organized (DL-186, third version)

IMG-02 is the campaign dashboard in miniature: **one wide, rounded card**, not layers. Top, a painted cover (the valley, under a light scrim that fades into the card's base)
with the campaign's name, **The Ashen Road**, a hairline with a brass diamond under it, and the party as a row of round portraits (a radio group: click, or the arrow keys,
Home and End). Below, on the card's dark base, two panels: the picked hero's card on paper (portrait that melts into the paper, name, class icon · Level 1, HP and AC chips; Kael is
Fighter · Level 1, 12 HP, 19 AC) and the **Quests** panel on dark glass: three real checkboxes ("Complete the encounter" is done at first, "Explore the ruins", "Recover the stolen goods"),
a count (`1 / 3`) and a brass rule between the title and the count that fills as quests are done (`--p`, default 0.333 so the first state needs no script). All three done: the count and rule turn brass.
Narrow frames (under 560px, measured on the frame) stack the two panels.

The campaign is a demo ("The Ashen Road"), the party and their portraits are the tutorial campaign's. `*-card.webp` (300px) are the card portraits; `<name>.webp` (144px) the round ones.
Details that keep it feeling like the app: the selected portrait gets a brass ring and lifts 3px; quest rows take a hover wash; the check ring presses in on `:active` (all 150 to 260ms,
transform, opacity and colour only, ease-out, off under reduced motion); picking a hero cross-fades the stacked cards (a transition, so it can be interrupted).
