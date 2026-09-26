# gptmail-vercel-api

Stateless GPTMail proxy API **with a built-in web panel** for Vercel.

The logic is intentionally simple and light — one thin client, the same shape
as the GPTMail client in
[zcode-account-hub](https://github.com/VolcharaVasiliy/zcode-account-hub):
a requests session + the short-lived inbox token + GPTMail cookies.

**On open the panel takes the token and the verified cookie by itself**
(`POST /api/bootstrap`): the session is restored from `localStorage`, the
inbox token is re-issued when needed, and the verified-session cookie
(`gm_browser_verified`) that GPTMail gives for **~24 hours** travels inside
the `state`. No more manual dances unless GPTMail actually demands its
Cloudflare check (then a single console-script paste per ~24 h).

## Web panel

Open the deployment root (`https://your-app.vercel.app/`):

- **on open** the panel automatically calls `/api/bootstrap` and shows the
  session pill with the exact time the ~24 h session ends (`session until 14:32`);
- **domain picker**: a button next to the prefix field opens a searchable list
  of active public domains (`/api/domains`) — type to filter, click to choose,
  "any (random)" to keep the old random behavior, or type a custom domain;
- one button creates a mailbox (identity username + chosen/random domain),
  the inbox auto-refreshes every 15 s, letters open in a full view, and there
  is a clear-inbox button;
- if a call hits `428 browser_verification_required`, the verification section
  opens by itself: click "copy console script", paste it into the F12 console
  on mail.chatgpt.org.uk, paste the token back — the session then lives ~24 h;
- "copy state" gives you the exact `{"state": ...}` body to reuse the session
  in curl/scripts (this is what zcode-account-hub round-trips).

## Design

This project keeps Vercel stateless and the client thin:

- Vercel stores no mailbox state on disk; the caller stores the returned
  `state` blob and sends it back with each request.
- `state` = `{base_url, language, auth: {token, email, expires_at}, cookies,
  last_email, updated_at}`. The inbox token inside it lives ~10 minutes and is
  refreshed automatically; the `gm_browser_verified` cookie lives ~24 h.
- One request helper handles headers, auth sync, the 401/403 re-issue and the
  428 verification signal — nothing else.

## GPTMail protocol (short version)

- Active public domains: `GET /api/domains/public?view=bootstrap` (paginated).
- A mailbox is claimed with `POST /api/inbox-token` (`email`,
  `include_emails`); the address itself is composed from
  `GET /api/generate-identity` + a chosen domain.
- `POST /api/inbox-token` answers `428 browser_verification_required` unless
  the request carries the `gm_browser_verified` cookie (Cloudflare Turnstile,
  sitekey `0x4AAAAAAD9zdhyrcm6dCJRt`, action `inbox_browser_verification`).
  GPTMail then sets `gm_sid` and binds the inbox token to it.
- `GET /api/emails`, `GET /api/email/{id}`, `DELETE /api/emails/clear` need
  the `x-inbox-token` header plus the matching `gm_sid` cookie.
- The inbox token lives ~10 minutes; this API re-issues it automatically, so
  the only thing that actually expires is the ~24 h verified cookie.

## Endpoints

- `GET /` — the web panel
- `GET /info` — service info: version, `turnstile_sitekey`, endpoint list
- `GET /health`
- `GET /tools/gptmail-token.js` — the console helper script
- `POST /api/bootstrap` — **open-the-panel call**: restore/issue the session
  (token + cookies, valid ~24 h). Reuses a still-valid token without any
  upstream request.
- `GET /api/domains` — active public domains for the picker
- `POST /api/verify-browser` — exchange a Turnstile token for the ~24 h cookie
- `POST /api/generate` — create a mailbox (`prefix`, `domain` optional)
- `POST /api/list` — inbox letters
- `POST /api/email` — read a single letter (full body) by `email_id`
- `POST /api/clear` — clear the inbox

All POST endpoints accept:

```json
{
  "state": {},
  "cookies": [],
  "base_url": "https://mail.chatgpt.org.uk",
  "language": "ru",
  "timeout": 20,
  "network_attempts": 3
}
```

`cookies` seeds additional session cookies on top of `state`.

`/api/generate` also accepts:

```json
{ "prefix": "", "domain": "" }
```

Response shape:

```json
{ "ok": true, "result": {}, "state": {} }
```

Requests made before verification answer `428` with
`browser_verification_required`, the current `turnstile_sitekey`, and the
partially-updated `state`.

Compatibility: the endpoint/result shapes are kept, so
[zcode-account-hub](https://github.com/VolcharaVasiliy/zcode-account-hub)
(`VercelGptMailClient`: `/api/generate`, `/api/list`, `/api/clear` + `state`
round-trip) keeps working unchanged.

## Auth

If `API_BEARER_TOKEN` is set in Vercel environment variables, every endpoint
requires:

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

1. Create a Vercel project from this repository (or just push — Vercel
   redeploys automatically).
2. Set `API_BEARER_TOKEN` in environment variables if you want protected access.
3. Deploy.

## Example

Daily flow: open `https://your-app.vercel.app/` — the panel itself takes the
token + cookie (good for ~24 h). If GPTMail demands verification, do the one
console-script paste; the pill shows when the session ends. Pick a domain in
the picker, click "create mailbox".

Raw API equivalent:

```bash
# session for the day (also happens automatically when the panel opens)
curl -X POST https://your-app.vercel.app/api/bootstrap \
  -H "Content-Type: application/json" -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{}'

# domains for the picker
curl https://your-app.vercel.app/api/domains

# create a mailbox on a chosen domain
curl -X POST https://your-app.vercel.app/api/generate \
  -H "Content-Type: application/json" -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"state": {}, "domain": "busmail.app"}'

# list the inbox (state from the previous response goes as-is)
curl -X POST https://your-app.vercel.app/api/list \
  -H "Content-Type: application/json" -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"state": {}}'
```
