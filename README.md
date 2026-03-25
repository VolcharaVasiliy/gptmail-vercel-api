# gptmail-vercel-api

Stateless GPTMail proxy API for Vercel.

## Design

This project keeps Vercel stateless:

- Vercel stores no mailbox state on disk.
- The caller stores the returned `state` blob.
- Each request sends the previous `state` and receives an updated `state`.
- The API only proxies GPTMail HTTP calls.

This avoids the local file/index model from the larger desktop project.

## Endpoints

- `GET /health`
- `POST /api/refresh-auth`
- `POST /api/generate`
- `POST /api/list`
- `POST /api/clear`

All POST endpoints accept:

```json
{
  "state": {},
  "base_url": "https://mail.chatgpt.org.uk",
  "language": "ru",
  "timeout": 20,
  "network_attempts": 4
}
```

`/api/generate` also accepts:

```json
{
  "prefix": "",
  "domain": ""
}
```

If `prefix` and `domain` are omitted or empty, GPTMail generates both values randomly.

`/api/refresh-auth`, `/api/list`, and `/api/clear` also accept:

```json
{
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

## Local run

```powershell
python -m uvicorn api.index:app --reload
```

## Deploy

1. Create a Vercel project from this repository.
2. Set `API_BEARER_TOKEN` in environment variables if you want protected access.
3. Deploy.

## Example

Generate a mailbox from an empty state:

```bash
curl -X POST https://your-app.vercel.app/api/generate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d "{}"
```

List inbox using the returned state:

```bash
curl -X POST https://your-app.vercel.app/api/list \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d "{\"state\": {\"base_url\": \"https://mail.chatgpt.org.uk\"}}"
```
