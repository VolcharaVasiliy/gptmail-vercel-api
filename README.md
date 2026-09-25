# gptmail-vercel-api

Stateless GPTMail proxy API **with a built-in web panel** for Vercel.

Updated for the 2026 GPTMail protocol (mail.chatgpt.org.uk): the old
server-side `POST /api/generate-email` endpoint is gone, and GPTMail now
requires a **verified browser session** before a mailbox can be created or
read.

## Web panel

Open the deployment root (`https://your-app.vercel.app/`) — a small control
panel serves there:

- the `state` (verified session + mailbox) is kept in browser localStorage,
  so the panel survives reloads;
- when a call hits `428 browser_verification_required`, the panel opens the
  verification section by itself: click "Скопировать консольный скрипт",
  paste it into the F12 console on mail.chatgpt.org.uk, then paste the
  token back into the panel;
- one button creates a mailbox (identity username + random active domain,
  custom prefix/domain optional), the inbox auto-refreshes every 15 s,
  letters open in a full view, and there is a clear-inbox button;
- "Скопировать state" gives you the exact `{"state": ...}` body to reuse
  the session in curl/scripts.

## Design

This project keeps Vercel stateless:

- Vercel stores no mailbox state on disk.
- The caller stores the returned `state` blob.
- Each request sends the previous `state` and receives an updated `state`.
- The `state` carries the GPTMail session cookies (`gm_browser_verified`,
  `gm_sid`) and the short-lived inbox token, so consecutive calls stay
  authenticated.

## Current GPTMail protocol (what changed)

- New mailboxes are composed client-side: identity username
  (`GET /api/generate-identity`) + random active public domain
  (`GET /api/domains/public?view=bootstrap`), then claimed with
  `POST /api/inbox-token`.
- `POST /api/inbox-token` answers `428 browser_verification_required`
  unless the request carries the `gm_browser_verified` cookie (Cloudflare
  Turnstile, sitekey `0x4AAAAAAD9zdhyrcm6dCJRt`, action
  `inbox_browser_verification`). GPTMail then sets `gm_sid` and binds the
  returned inbox token to it.
- `GET /api/emails`, `DELETE /api/emails/clear` and
  `GET /api/email/{id}` require the `x-inbox-token` header plus the
  matching `gm_sid` cookie.
- The inbox token lives ~10 minutes; this API refreshes it automatically.

## How the browser verification is handled

A serverless function cannot solve Turnstile itself, so a verified session
must come from the caller. The web panel and the ready-made console script
**[tools/gptmail-token.js](tools/gptmail-token.js)** (also served at
`/tools/gptmail-token.js` for the panel's copy button) cover it:

1. Open mail.chatgpt.org.uk in a browser.
2. Press F12 → Console, paste the script contents (the panel can copy it
   for you), hit Enter.
3. A small Turnstile widget flashes top-right, and the console copies
   either the fresh token or (if you filled in `API_BASE` inside the
   script) the ready-to-use `state` to the clipboard.

That one paste covers roughly a day of use (`gm_browser_verified` is valid
~24 h). Afterwards the API keeps the session alive by itself: inbox tokens
are short-lived but are refreshed automatically, so no further browser
steps are needed until the verification cookie expires.

Manually, the same result comes from either:

1. **Cookie seeding.** Open mail.chatgpt.org.uk, copy the
   `gm_browser_verified` cookie value (DevTools → Application → Cookies —
   the cookie is httpOnly, so `document.cookie` won't show it), and send
   it in the request:

   ```json
   { "cookies": [{ "name": "gm_browser_verified", "value": "eyJ..." }] }
   ```

2. **Turnstile token exchange.** Render a Cloudflare Turnstile widget with
   the sitekey returned by `GET /info` (action
   `inbox_browser_verification`) on any page served from
   mail.chatgpt.org.uk, then POST the token to `/api/verify-browser`. The
   API exchanges it via GPTMail's `POST /api/browser-verification`, keeps
   the resulting cookies in `state`, and every later call reuses them.
   (The widget cannot be rendered on the API's own domain — GPTMail locks
   the sitekey to mail.chatgpt.org.uk with Turnstile error 110200.)

Requests made before verification answer `428` with
`browser_verification_required`, the current `turnstile_sitekey`, and the
partially-updated `state`.

CORS is open (`*`), so the console script can talk to your deployment
straight from the GPTMail page.

## Endpoints

- `GET /` — the web panel
- `GET /info` — service info: version, `turnstile_sitekey`, endpoint list
- `GET /health`
- `GET /tools/gptmail-token.js` — the console helper script
- `POST /api/verify-browser` — exchange a Turnstile token for session cookies
- `POST /api/refresh-auth`
- `POST /api/generate`
- `POST /api/list`
- `POST /api/email` — read a single letter (full body) by id
- `POST /api/clear`

All POST endpoints accept:

```json
{
  "state": {},
  "cookies": [],
  "turnstile_token": "",
  "base_url": "https://mail.chatgpt.org.uk",
  "language": "ru",
  "timeout": 20,
  "network_attempts": 4
}
```

`cookies` seeds additional session cookies on top of `state`.

`/api/generate` also accepts:

```json
{
  "prefix": "",
  "domain": ""
}
```

If `prefix`/`domain` are omitted, the API picks an identity username and a
random active public domain for you.

`/api/refresh-auth`, `/api/list`, and `/api/clear` also accept:

```json
{
  "email": "mailbox@example.com"
}
```

`/api/email` additionally requires:

```json
{
  "email_id": "m37_8472f089...",
  "email": "mailbox@example.com"
}
```

Response shape:

```json
{
  "ok": true,
  "result": {},
  "state": {}
}
```

## Auth

If `API_BEARER_TOKEN` is set in Vercel environment variables, every POST endpoint requires:

```http
Authorization: Bearer YOUR_TOKEN
```

`GPTMAIL_TURNSTILE_SITEKEY` can override the default Turnstile sitekey if
GPTMail rotates it.

## Local run

```powershell
python -m uvicorn api.index:app --reload
```

## Deploy

1. Create a Vercel project from this repository.
2. Set `API_BEARER_TOKEN` in environment variables if you want protected access.
3. Deploy.

## Example

Daily flow with the web panel: open `https://your-app.vercel.app/`, click
"Создать ящик" — on the first 428 the panel opens the verification section;
copy the console script there, run it on mail.chatgpt.org.uk, paste the
token, and continue. The panel keeps the session and mailbox in the
browser afterwards.

The same flow over the raw API — exchange the token from the console
script:

```bash
curl -X POST https://your-app.vercel.app/api/verify-browser \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"turnstile_token": "0.zgk..."}'
```

The response `state` is your session for the day — reuse it in every
request body.

Generate a mailbox from that state:

```bash
curl -X POST https://your-app.vercel.app/api/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"state": {}}'
```

List the inbox using the returned state:

```bash
curl -X POST https://your-app.vercel.app/api/list \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"state": {}}'
```

(the `state` from the previous response goes into the body as-is)

Read one letter:

```bash
curl -X POST https://your-app.vercel.app/api/email \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"state": {}, "email_id": "m37_8472f089..."}'
```
