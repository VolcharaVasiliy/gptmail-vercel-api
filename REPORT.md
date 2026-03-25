# Report - gptmail-vercel-api - 2026-03-25

## Summary
- Created a new minimal GitHub/Vercel-ready project for stateless GPTMail API usage.
- Split the reusable HTTP client from the desktop tool and removed local mailbox index/state file dependencies from the server boundary.
- Added a FastAPI entrypoint for Vercel with bearer-token protection.
- Created the GitHub repository `VolcharaVasiliy/gptmail-vercel-api` and prepared the project for first push.
- Pushed the initial commit to `origin/main`.
- Switched the repository visibility to `public`.
- Added explicit README note that empty `prefix` and `domain` keep random mailbox/domain generation.

## Files
- `gptmail_api/client.py` - stateless GPTMail client with `from_state_payload()` and `export_state()`.
- `gptmail_api/parsers.py` - message/link/code extraction helpers.
- `api/index.py` - FastAPI Vercel entrypoint with `refresh-auth`, `generate`, `list`, and `clear`.
- `requirements.txt` - minimal runtime dependencies.
- `vercel.json` - Vercel function configuration.
- `README.md` - deployment and API contract.

## Rationale
- The original desktop `gptmail_tool.py` was file-backed and unsuitable as a serverless boundary.
- Returning `state` on every response keeps Vercel stateless and lets the caller store all mailbox/session data locally.
- The API surface stays intentionally small to avoid long-running serverless polling paths.
- Upstream failures are translated into explicit JSON HTTP errors instead of raw framework 500s.

## Issues
- No automated tests yet.
- `wait_for_email` was intentionally not exposed in the Vercel API because it is a blocking polling path and a poor fit for serverless duration limits.

## Functions
- `GptMailClient.from_state_payload` (`gptmail_api/client.py`) - reconstruct client state from caller-provided JSON.
- `GptMailClient.export_state` (`gptmail_api/client.py`) - return caller-storable mailbox/session state.
- `generate` (`api/index.py`) - create a mailbox from current or empty state.
- `list_emails` (`api/index.py`) - fetch inbox with caller-owned state.
- `clear_emails` (`api/index.py`) - clear inbox with caller-owned state.

## Next steps
- Add a small local smoke script for endpoint verification.
- Add request schema validation tests.

## Verification
- `F:\DevTools\Python311\python.exe -m py_compile api\index.py gptmail_api\client.py gptmail_api\parsers.py`
- FastAPI import smoke: `from api.index import app`
- Stateful round-trip smoke: `GptMailClient.from_state_payload(...).export_state()`
- Endpoint smoke with `fastapi.testclient`:
  - `GET /health` -> `200`
  - `POST /api/generate` -> `200`
  - `POST /api/generate` then `POST /api/list` -> `200`
- Git checks:
  - `git status --short --branch` -> `main...origin/main`
  - `git log -1 --oneline` -> `06ff776 Initial stateless GPTMail Vercel API`
  - GitHub visibility check -> `VolcharaVasiliy/gptmail-vercel-api`, `private=False`, `visibility=public`
