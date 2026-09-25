# gptmail-vercel-api

Stateless GPTMail proxy API for Vercel.

Updated for the 2026 GPTMail protocol (mail.chatgpt.org.uk): the old
server-side `POST /api/generate-email` endpoint is gone, and GPTMail now
requires a **verified browser session** before a mailbox can be created or
read.

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
must come from the caller. The easiest path is the ready-made console
script **[tools/gptmail-token.js](tools/gptmail-token.js)**:

1. Open https://mail.chatgpt.org.uk in a browser.
2. Press F12 → Console, paste the script contents, hit Enter.
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
   the sitekey returned by `GET /` (action `inbox_browser_verification`)
   on any page served from mail.chatgpt.org.uk, then POST the token to
   `/api/verify-browser`. The API exchanges it via GPTMail's
   `POST /api/browser-verification`, keeps the resulting cookies in
   `state`, and every later call reuses them.

Requests made before verification answer `428` with
`browser_verification_required`, the current `turnstile_sitekey`, and the
partially-updated `state`.

CORS is open (`*`), so the console script can talk to your deployment
straight from the GPTMail page.

## Endpoints

- `GET /health`
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

Daily flow with the console script (API_BASE left empty — token mode):

```bash
# paste tools/gptmail-token.js into the F12 console on mail.chatgpt.org.uk,
# then exchange the copied token:
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
