# Third parties, tracking and bundled assets

Audit of 15 Oct 2026 (backlog DL-75 third-party embeds, DL-78 tracking, DL-76 copyright on images).
It is the factual input for the Privacy Policy, Cookies Policy and cookie-consent decisions (DL-69, DL-77, DL-82).
`tests/test_third_party_audit.py` keeps the "nothing third-party in the browser" findings true.

## 1. What the browser loads from other sites: nothing

- No external script, stylesheet, font, image, iframe, embed or tracking pixel in any template, stylesheet or script
  (the test fails if one is added).
- Fonts (Newsreader and Lato; Fraunces, Inter and IBM Plex Mono until DL-180 part 26) are self-hosted in `static/fonts/` (SIL Open Font License, text in `LICENSES.txt`).
- The one library, lottie-web (map fog animation), is vendored in `static/vendor/` (MIT, licence file next to it).
- The Content-Security-Policy enforces this: `default-src 'self'`; scripts, images, fonts and media only from the app itself.
  The only extra origin the page may *connect* to is your Supabase project (direct-to-storage uploads and Realtime).
- Referrer-Policy is `same-origin`; camera, microphone, geolocation, payment and USB are switched off.

## 2. Tracking: none

No analytics, advertising, session-recording or fingerprinting code, and no third-party cookies. Nothing is sent about
what a visitor does. The server logs requests (method, route, status, time, user id, request id; never query strings or
form bodies). Error tracking (Sentry) is **off** unless `SENTRY_DSN` is set and `sentry-sdk` is installed, and it sends no
personal data.

## 3. What the app stores in the visitor's browser

| Where | Name | Purpose | Lifetime |
|---|---|---|---|
| Cookie | `__Host-oaken` (production) / `oaken_session` (local) | Login session: user id, username, CSRF token, last campaign, one-time flags. HttpOnly, SameSite=Lax, Secure | Until the browser closes; 30 days only if "remember me" is ticked |
| localStorage | `oaken-theme` | Light/dark choice | Until cleared |
| localStorage | `oaken-sidebar-collapsed` | Sidebar open or collapsed | Until cleared |
| localStorage | `oaken-map-dock-tucked` | Phone map toolbar tucked or not | Until cleared |
| localStorage | `oaken-map-modifier-hint-dismissed`, `oaken-map-nav-hint-dismissed`, `oaken-map-space-hint-dismissed` | "Don't show this tip again" flags | Until cleared |
| sessionStorage | `oaken-fullscreen` | Phone map fullscreen state | Until the tab closes |

The cookie is strictly necessary (it is the login). The localStorage entries are interface preferences; they never leave
the browser and carry no personal data. No consent banner is needed for these on current guidance, but the final answer
belongs to DL-80 (applicable local laws) and DL-82.

## 4. What the server sends to other services

| Service | What | Personal data |
|---|---|---|
| Supabase (database, file storage, Realtime) | Everything the app stores: accounts, campaigns, characters, images, live-update messages | Yes: usernames, password hashes, campaign content |
| Vercel (hosting) | Serves the app; sees visitor IP addresses and request logs | IP addresses |
| dnd5eapi.co | Server-side lookups for the rules reference (spell, monster, item name) | None: the visitor's browser never contacts it |
| Sentry (optional, off by default) | Error reports | None sent |
| Email provider (optional, off until `MAIL_HOST` is set; provider not chosen yet) | The recipient address and the message (a one-time confirmation link) when a user asks to confirm their email | Yes: the email address the user typed. No tracking pixels or tracked links are added |

## 5. Bundled assets and their licences

| Asset | Where | Status |
|---|---|---|
| Newsreader and Lato fonts | `static/fonts/` | Verified: SIL OFL 1.1, bundled licence text. Newsreader from `@fontsource-variable/newsreader` 5.3.0, Lato 2.015 from `lato-font` 3.0.0, both used unmodified (Lato has a Reserved Font Name, so the files are not subset or renamed). Replaced Fraunces, Inter and IBM Plex Mono on 21 Nov 2026 (DL-180 part 26) |
| lottie-web | `static/vendor/` | Verified: MIT, licence file bundled |
| Map pin icon: skull | `static/icons/pins/skull.svg` | Replaced on 20 Nov 2026 (DL-180 part 21): "Fanged Skull" by Lorc, [game-icons.net](https://game-icons.net/1x1/lorc/fanged-skull.html), CC BY 3.0, credited on the About page. The icon it replaced (SVG Repo, title `monster-skull`, id 499291) is listed on SVG Repo under the GPL, author nagoshiashumari, collection "Rpg Game Filled Icons", which does not suit a closed app |
| Map pin icons: sword, hand, paw | `static/icons/pins/` | Replaced on 21 Nov 2026 (DL-180 part 22): "Broadsword", "Hand" and "Flat paw print" by Lorc, [game-icons.net](https://game-icons.net), CC BY 3.0, credited on the About page. The files they replaced came from SVG Repo with no author or licence recorded |
| Interface icons: characters, battle, dice | `templates/_icons.html` | Replaced on 21 Nov 2026 (DL-180 part 22): "Character" and "Dice twenty faces twenty" by Delapouite, "Crossed swords" by Lorc, [game-icons.net](https://game-icons.net), CC BY 3.0, credited on the About page. The icons they replaced carried SVG Repo headers |
| Class icons (13) | `static/icons/classes.svg` | Source per owner: SVG Repo or Flaticon, otherwise drawn by Claude. **Open:** the SVG files carry no author or licence, so which icon came from where is not recorded |
| Condition icons (15) | `static/icons/conditions.svg` | [game-icons.net](https://game-icons.net), Creative Commons Attribution 3.0 (DL-173). Credited by author on the About page: Lorc, Delapouite, Skoll, Cathelineau, Sbed. The SVG paths come from the `@iconify-json/game-icons` package, recoloured with `currentColor`; the sprite also holds the app's own `custom` and `plus` symbols |
| Fog animation | `static/fog/fog-smoke.json` | [Fog / Smoke Animation by Ladon Sklyarov, LottieFiles](https://lottiefiles.com/free-animation/fog-smoke-gcgRd1137Q), under the Lottie Simple License: commercial use and changes allowed, no attribution required but strongly encouraged (if given, it must be visible to users). Owner plans to replace it with his own work later |
| Logo and favicons | `static/logo/` | Assumed the owner's own work (DL-90 redraw is the owner's task) |
| Paper and oak textures (DL-180) | `static/assets/textures/` | Supplied by the owner in `Assets.zip` (light and dark parchment, dark wood) and processed by `scripts/build_oaken_assets.py` (seamless tiles). The two burned-edge sheets from the same zip are no longer used: the scorch along a paper panel's edge on hover is drawn in CSS (`.o-burn::before` and `.o-burn::after` in `static/css/oaken.css`). **Open:** where the owner got them and under what licence was not recorded. The light oak file is a placeholder re-graded from the dark oak, not a supplied asset |
| Background paintings (DL-180) | `static/assets/backgrounds/` | Supplied by the owner in `Background_Images.zip`; file names show Unsplash downloads from Birmingham Museums Trust (2), Boston Public Library and Europeana (2). Unsplash licence (free use, no credit required, credit appreciated). **Open:** the painting titles and artists were not recorded; check each Unsplash page that the work itself is public domain and add a credit line on the About page if the owner wants one |

### What is still open (DL-76)

- **Flaticon icons.** Flaticon's free licence requires a visible credit ("Icon by *author* from flaticon.com") for each
  icon, unless the downloader has a paid plan. If the owner has a paid Flaticon plan, nothing is needed; if not, find
  the authors (Flaticon account, download history) and list them on the About page (DL-25).
- **SVG Repo icons.** Each icon's page states its licence and author. Check the ones used and add a credit where the
  licence is CC BY. CC0 and MIT icons need nothing.
- **Fog animation credit.** Optional, but a line on the About page (DL-25) is the polite thing: "Fog animation by Ladon
  Sklyarov, LottieFiles".
- **Icons drawn by Claude** need no credit.

Record the answers in the table above. Until the Flaticon and SVG Repo licences are checked, treat DL-76 as open.

### Update of 31 Oct 2026 (DL-171)

- **SRD credit (new finding, now fixed).** The rules reference shows text from the D&D System Reference Document 5.2.1, which
  Wizards of the Coast released under Creative Commons Attribution 4.0 (CC BY 4.0). That licence asks for the credit
  wherever the text is used, and the About page only said "open SRD". Added: the full notice on `/about#credits` and a
  short credit with a link under the Rules Reference. The wording is the one I know from the SRD's legal page; compare it
  with the first pages of the SRD 5.2.1 PDF (dndbeyond.com/srd) before launch. A test checks both are present.
- **Fog animation, fonts, lottie-web** are credited on `/about#credits`.
- **Icons.** I compared the paths of the class icons, condition icons and pin icons with the public icon sets Phosphor,
  Lucide, Tabler and Font Awesome Free (current npm releases): none match, so they were not copied unchanged from
  those sets. That leaves SVG Repo and Flaticon, as the owner said, and the files carry no author. The only clue is the
  SVG Repo title in the skull pin (`monster-skull`); the sword, hand and paw pins have none. **Only the owner can close
  this**: search those titles on svgrepo.com (or check the Flaticon download history), note the author and licence, and
  add a credit line under "Credits and licences" for every CC BY icon. If an icon's licence cannot be found, redraw it
  (DL-90).
- Links out (licences, sources) are allowed on the About and Rules Reference pages only, as plain links that load nothing
  until clicked; `tests/test_third_party_audit.py` still fails on any external script, style, font, image or embed.


### Update of 20 Nov 2026 (DL-180 part 21, licence check of the pin icons)

- **Skull pin: the licence was a problem, now fixed.** The SVG Repo page of the skull (`monster-skull`, svgrepo.com/svg/499291) shows the **GPL** licence, author nagoshiashumari. A copyleft licence is not a fit for the app, so the icon was swapped for "Fanged Skull" by Lorc (game-icons.net, CC BY 3.0, the same set and author already credited for the condition icons). The credit is on `/about#credits`.
- **Sword, hand and paw pins: still open.** The files carry the SVG Repo header but no title, so the page cannot be found from here. To close them: on svgrepo.com search the icon, open its page and read the licence and author. A GPL icon should be replaced; CC0 and MIT need nothing; CC BY needs a credit line. Or replace all three from game-icons.net (CC BY 3.0, already credited), which also makes the four pins one style.
- **Interface icons with an SVG Repo header** (in `templates/_icons.html`, files that kept their 800 px size and `<?xml` line): `scroll-text`, `swords`, `dices`. Same check, same remedy. The other icons in that file are drawn on the 24 px stroke grid used by the rest of the app.
- **Background paintings** (`static/assets/backgrounds/`): the file names point to Unsplash downloads of Birmingham Museums Trust, Boston Public Library and Europeana items, which those institutions publish as public domain or CC0. No credit is required; a line on the About page naming the museums would be a courtesy. The owner should confirm each original on the Unsplash page before launch.


### Update of 21 Nov 2026 (DL-180 part 22, remaining SVG Repo icons replaced)

- **Closed:** the sword, hand and paw pins and the three interface icons that still carried SVG Repo headers (`scroll-text` for Characters, `swords` for Battle, `dices` for Dice) were replaced with game-icons.net icons (CC BY 3.0, the set and licence already used for the condition icons and the skull pin). All four pins are now one style. The authors are credited on `/about#credits`: Lorc (Fanged Skull, Broadsword, Hand, Flat paw print, Crossed swords) and Delapouite (Character, Dice twenty faces twenty). Each file or icon block also carries a short credit comment.
- **No SVG Repo icon is left in the pins or in `templates/_icons.html`.** `tests/test_third_party_audit.py` fails if an SVG Repo header comes back in either place.
- **Still open for the owner:** (1) the class icons in `static/icons/classes.svg` (SVG Repo or Flaticon, no author recorded); (2) the original of each background painting on Unsplash.


### Update of 21 Nov 2026 (DL-180 part 26, typefaces)

- Fraunces, Inter and IBM Plex Mono are replaced by **Newsreader** (titles and headings) and **Lato** (everything else). Both are under the SIL Open Font License 1.1 and self-hosted in `static/fonts/` with the licence text in `LICENSES.txt`; no request goes to a font service. Newsreader files come from the `@fontsource-variable/newsreader` package (Latin, weight and optical size axes). Lato files are Lato 2.015 from the `lato-font` package (Regular, Italic, Medium, Semibold; Lato 2 is used because Google's Lato 1 has no Medium or Semibold). Lato carries a Reserved Font Name, so its files are used exactly as published, not subset or renamed. The credit is on `/about#credits`.

