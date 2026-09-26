from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from gptmail_api import (
    DEFAULT_BASE_URL,
    DEFAULT_LANGUAGE,
    DEFAULT_TURNSTILE_SITEKEY,
    BrowserVerificationRequired,
    GptMailClient,
)

app = FastAPI(title="GPTMail Vercel API", version="0.3.1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The browser console helper (tools/gptmail-token.js) can exchange a Turnstile
# token directly from the GPTMail page, so the API must be callable cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


def turnstile_sitekey() -> str:
    return str(os.getenv("GPTMAIL_TURNSTILE_SITEKEY") or "").strip() or DEFAULT_TURNSTILE_SITEKEY


class BaseRequest(BaseModel):
    state: dict[str, Any] | None = None
    cookies: list[dict[str, Any]] | None = None
    turnstile_token: str = ""
    base_url: str = DEFAULT_BASE_URL
    language: str = DEFAULT_LANGUAGE
    timeout: float = 20.0
    network_attempts: int = 3


class GenerateRequest(BaseRequest):
    prefix: str = ""
    domain: str = ""


class EmailRequest(BaseRequest):
    email: str = ""


class SingleEmailRequest(EmailRequest):
    email_id: str = ""


def require_api_bearer(authorization: str | None) -> None:
    expected = str(os.getenv("API_BEARER_TOKEN") or "").strip()
    if not expected:
        return
    provided = str(authorization or "").strip()
    if provided != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid API bearer token")


def build_client(request: BaseRequest) -> GptMailClient:
    client = GptMailClient.from_state(
        request.state,
        base_url=request.base_url or DEFAULT_BASE_URL,
        language=request.language or DEFAULT_LANGUAGE,
        timeout=float(request.timeout or 20.0),
        network_attempts=int(request.network_attempts or 3),
        turnstile_sitekey=turnstile_sitekey(),
    )
    # Allow callers to seed a verified browser session without the full state:
    # paste cookies (e.g. gm_browser_verified / gm_sid) straight into the request.
    for cookie in request.cookies or []:
        if not isinstance(cookie, dict) or not cookie.get("name"):
            continue
        client.session.cookies.set(
            str(cookie["name"]),
            str(cookie.get("value") or ""),
            domain=str(cookie.get("domain") or client.base_url),
            path=str(cookie.get("path") or "/"),
        )
    return client


class VerificationNeeded(Exception):
    """Carries the client so the 428 response can echo the updated state."""

    def __init__(self, exc: BrowserVerificationRequired, client: GptMailClient) -> None:
        super().__init__(str(exc))
        self.exc = exc
        self.client = client


def verification_response(client: GptMailClient, exc: BrowserVerificationRequired) -> JSONResponse:
    return JSONResponse(
        status_code=428,
        content={
            "ok": False,
            "error": "browser_verification_required",
            "detail": str(exc),
            "turnstile_sitekey": exc.sitekey or turnstile_sitekey(),
            "hint": (
                "Open mail.chatgpt.org.uk, run the console script from "
                "/tools/gptmail-token.js (the panel copies it for you) and POST "
                "the token to /api/verify-browser. The session then lives ~24 h."
            ),
            "state": client.export_state(),
        },
    )


def handle(client: GptMailClient, action) -> Any:
    try:
        result = action()
    except BrowserVerificationRequired as exc:
        return verification_response(client, exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "result": result, "state": client.export_state()}


def _serve_project_file(relative_path: str, media_type: str) -> Response:
    file_path = PROJECT_ROOT / relative_path
    try:
        return Response(content=file_path.read_bytes(), media_type=media_type)
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"File not found: {relative_path}") from exc


@app.get("/")
def web_ui() -> Response:
    return _serve_project_file("site/index.html", "text/html; charset=utf-8")


@app.get("/tools/gptmail-token.js")
def console_helper() -> Response:
    return _serve_project_file("tools/gptmail-token.js", "text/javascript; charset=utf-8")


@app.get("/info")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "gptmail-vercel-api",
        "version": "0.3.1",
        "auth_enabled": bool(str(os.getenv("API_BEARER_TOKEN") or "").strip()),
        "turnstile_sitekey": turnstile_sitekey(),
        "endpoints": [
            "/health",
            "/api/bootstrap",
            "/api/domains",
            "/api/verify-browser",
            "/api/generate",
            "/api/list",
            "/api/email",
            "/api/clear",
        ],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.post("/api/bootstrap")
def bootstrap(request: BaseRequest, authorization: str | None = Header(default=None)) -> Any:
    """Open-the-panel call: return a session that lives ~24 h.

    Reuses the stored token/cookies when they are still valid; otherwise
    issues a fresh inbox token (and carries the verified-session cookie that
    lasts ~24 h inside the returned state).
    """
    require_api_bearer(authorization)
    client = build_client(request)

    def action() -> dict[str, Any]:
        if client.should_refresh():
            client.refresh_auth()
        return {
            "verified": True,
            "email": client.last_email or client.token_email,
            "expires_at": client.expires_at,
            "valid_until": client.verified_until(),
        }

    return handle(client, action)


@app.get("/api/domains")
def public_domains(
    language: str = DEFAULT_LANGUAGE, authorization: str | None = Header(default=None)
) -> Any:
    """Active public domains for the panel's domain picker."""
    require_api_bearer(authorization)
    client = GptMailClient(language=language or DEFAULT_LANGUAGE, turnstile_sitekey=turnstile_sitekey())
    return handle(client, lambda: {"domains": client.list_domains()})


@app.post("/api/verify-browser")
def verify_browser(request: BaseRequest, authorization: str | None = Header(default=None)) -> Any:
    require_api_bearer(authorization)
    client = build_client(request)
    return handle(client, lambda: {"verified": True, "raw": client.verify_browser(request.turnstile_token)})


@app.post("/api/generate")
def generate(request: GenerateRequest, authorization: str | None = Header(default=None)) -> Any:
    require_api_bearer(authorization)
    client = build_client(request)
    return handle(
        client,
        lambda: client.generate_email(
            prefix=request.prefix or None,
            domain=request.domain or None,
        ),
    )


@app.post("/api/list")
def list_emails(request: EmailRequest, authorization: str | None = Header(default=None)) -> Any:
    require_api_bearer(authorization)
    client = build_client(request)
    return handle(client, lambda: client.list_emails(request.email or None))


@app.post("/api/email")
def get_email(request: SingleEmailRequest, authorization: str | None = Header(default=None)) -> Any:
    require_api_bearer(authorization)
    client = build_client(request)
    return handle(client, lambda: client.get_email(request.email_id, request.email or None))


@app.post("/api/clear")
def clear_emails(request: EmailRequest, authorization: str | None = Header(default=None)) -> Any:
    require_api_bearer(authorization)
    client = build_client(request)
    return handle(client, lambda: client.clear_emails(request.email or None))
