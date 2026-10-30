# What Campaign Ledger collects about a person

This is the working inventory behind "only collect what is necessary" (DL-84). It describes what the code does today, so it can feed the Privacy Policy and Cookies pages. `tests/test_data_inventory.py` fails when a column is added to `users` without being listed here, which keeps it honest.

## The account (`users` table)
| Column | Why it is kept | Who can see it |
|---|---|---|
| `username` | Sign-in name and the link of the public profile. | Other signed-in people at a shared table; the profile page follows the visibility setting. |
| `password_hash` | Checking the password at sign-in. Only a salted hash is stored; empty for Google-only accounts. | Nobody. Never exported. |
| `email`, `email_verified_at` | Account recovery and confirmation mail. Required at sign-up. | Only the owner. Never shown to other players. |
| `google_sub` | Google's stable id for the person, kept only when they sign in with Google. | Nobody. |
| `display_name`, `bio`, `avatar_path` | Optional profile details the person chooses to add. | Follows `profile_visibility`. |
| `profile_visibility` | Who may open the public profile: everyone signed in, people they play with, or only them. | The owner. |
| `profile_setup_at` | Remembers that the one-time profile page was saved or skipped. | Nobody. |
| `email_confirm_required` | Whether this account still has to confirm its email before it can create or join a campaign. Set at sign-up, never changed by the person. | Nobody. |
| `cookies_accepted_at` | When the "I accept the cookies" box was ticked at sign-up (the site only sets its one sign-in cookie). | Nobody. |
| `terms_accepted_at` | When the account was made, which counts as agreeing to the Terms and Privacy page (the first tick box on the sign-up page). | Nobody. |
| `session_version` | Lets "sign out everywhere" end other devices' sessions. | Nobody. |
| `created_at` | When the account was made. | Nobody. |

## Things people create
Campaigns, characters, maps, notes, treasury entries, dice rolls and uploaded images belong to the campaign or to the person who made them. A person can download all of it from Account settings (JSON) and delete the account. Deleting removes the account and the characters they keep in Your characters; a campaign they alone own is either handed to another member (their choice) or deleted with it.

## What is deliberately not collected
No analytics, advertising or tracking scripts, and no third-party fonts or embeds (the Content-Security-Policy only allows the app's own files). No phone number, real name, birth date or address is asked for.

## IP addresses
Raw IP addresses are not stored. For sign-in and sign-up throttling the server keeps a keyed hash of the address (HMAC with the app secret) in `login_attempts`, and rows older than 24 hours are deleted. Normal request logs carry a request id, method, path and status.

## Cookies and browser storage
- One session cookie (`__Host-ledger` on https), HttpOnly, SameSite=Lax, Secure. It keeps the person signed in for up to 30 days when "remember me" is on, and ends with the browser session when it is off. It also carries the CSRF token.
- `localStorage`, only on the person's own device and never sent anywhere: theme, sidebar collapsed, map dock tucked, and three "already shown this tip" flags.

## Sent to other services
- **Supabase** (database and file storage) holds everything above.
- **Vercel** hosts the app and holds the request logs.
- **Google** only when the person chooses Google sign-in: name and verified email come back to us; nothing is sent to Google except the sign-in request.
- **Email provider**, when configured, receives the address and the text of confirmation and password-reset mail.
