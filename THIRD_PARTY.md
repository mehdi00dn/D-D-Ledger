# Third parties, tracking and bundled assets

Audit of 15 Oct 2026 (backlog DL-75 third-party embeds, DL-78 tracking, DL-76 copyright on images).
It is the factual input for the Privacy Policy, Cookies Policy and cookie-consent decisions (DL-69, DL-77, DL-82).
`tests/test_third_party_audit.py` keeps the "nothing third-party in the browser" findings true.

## 1. What the browser loads from other sites: nothing

- No external script, stylesheet, font, image, iframe, embed or tracking pixel in any template, stylesheet or script
  (the test fails if one is added).
- Fonts (Fraunces, Inter, IBM Plex Mono) are self-hosted in `static/fonts/` (SIL Open Font License, text in `LICENSES.txt`).
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
| Cookie | `__Host-ledger` (production) / `ledger_session` (local) | Login session: user id, username, CSRF token, last campaign, one-time flags. HttpOnly, SameSite=Lax, Secure | Until the browser closes; 30 days only if "remember me" is ticked |
| localStorage | `ledger-theme` | Light/dark choice | Until cleared |
| localStorage | `ledger-sidebar-collapsed` | Sidebar open or collapsed | Until cleared |
| localStorage | `ledger-map-dock-tucked` | Phone map toolbar tucked or not | Until cleared |
| localStorage | `ledger-map-modifier-hint-dismissed`, `ledger-map-nav-hint-dismissed`, `ledger-map-space-hint-dismissed` | "Don't show this tip again" flags | Until cleared |
| sessionStorage | `ledger-fullscreen` | Phone map fullscreen state | Until the tab closes |

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

## 5. Bundled assets and their licences

| Asset | Where | Status |
|---|---|---|
| Fraunces, Inter, IBM Plex Mono fonts | `static/fonts/` | Verified: SIL OFL 1.1, bundled licence text |
| lottie-web | `static/vendor/` | Verified: MIT, licence file bundled |
| Map pin icons: skull, sword, hand, paw | `static/icons/pins/` | Source per owner: SVG Repo (the files carry SVG Repo headers). **Open:** SVG Repo licences are per icon (CC0, MIT, CC BY, ...) and the original author and licence of each were not recorded |
| Class icons (13) | `static/icons/classes.svg` | Source per owner: SVG Repo or Flaticon, otherwise drawn by Claude. **Open:** the SVG files carry no author or licence, so which icon came from where is not recorded |
| Condition icons (15) | `static/icons/conditions.svg` | Same as the class icons |
| Fog animation | `static/fog/fog-smoke.json` | [Fog / Smoke Animation by Ladon Sklyarov, LottieFiles](https://lottiefiles.com/free-animation/fog-smoke-gcgRd1137Q), under the Lottie Simple License: commercial use and changes allowed, no attribution required but strongly encouraged (if given, it must be visible to users). Owner plans to replace it with his own work later |
| Logo and favicons | `static/logo/` | Assumed the owner's own work (DL-90 redraw is the owner's task) |

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
