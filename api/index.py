from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from gptmail_api import (
    DEFAULT_BASE_URL,
    DEFAULT_LANGUAGE,
    DEFAULT_TURNSTILE_SITEKEY,
    BrowserVerificationRequired,
    GptMailClient,
)

app = FastAPI(title="GPTMail Vercel API", version="0.2.0")

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
    network_attempts: int = Field(default=4, ge=1, le=10)


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
    sitekey = turnstile_sitekey()
    if request.state:
        client = GptMailClient.from_state_payload(
            request.state,
            timeout=request.timeout,
            turnstile_sitekey=sitekey,
        )
        client.base_url = str(request.base_url or client.base_url).rstrip("/")
        client.language = str(request.language or client.language)
        client.timeout = float(request.timeout)
        client.network_attempts = max(1, int(request.network_attempts))
    else:
        client = GptMailClient(
            base_url=request.base_url,
            language=request.language,
            timeout=request.timeout,
            network_attempts=request.network_attempts,
            turnstile_sitekey=sitekey,
        )

    # Allow callers to seed a verified browser session without the full state:
    # paste cookies (e.g. gm_browser_verified / gm_sid) straight into the request.
    host = str(urlsplit(client.base_url).hostname or "")
    for cookie in request.cookies or []:
        if not isinstance(cookie, dict) or not cookie.get("name"):
            continue
        client.session.cookies.set(
            str(cookie["name"]),
            str(cookie.get("value") or ""),
            domain=str(cookie.get("domain") or host),
            path=str(cookie.get("path") or "/"),
        )
    return client


class VerificationNeeded(Exception):
    """Carries the client so the 428 response can echo the updated state."""

    def __init__(self, exc: BrowserVerificationRequired, client: GptMailClient) -> None:
        super().__init__(str(exc))
        self.exc = exc
        self.client = client


def response_payload(*, client: GptMailClient, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "result": result,
        "state": client.export_state().as_dict(),
    }


def verification_response(client: GptMailClient, exc: BrowserVerificationRequired) -> JSONResponse:
    return JSONResponse(
        status_code=428,
        content={
            "ok": False,
            "error": "browser_verification_required",
            "detail": str(exc),
            "turnstile_sitekey": exc.sitekey or turnstile_sitekey(),
            "hint": (
                "Render a Cloudflare Turnstile widget with this sitekey (action "
                "'inbox_browser_verification'), then POST the token to /api/verify-browser, "
                "or seed the request with a gm_browser_verified cookie copied from "
                "mail.chatgpt.org.uk. The updated cookies come back in 'state'."
            ),
            "state": client.export_state().as_dict(),
        },
    )


def run(client: GptMailClient, action) -> dict[str, Any]:
    try:
        return action()
    except BrowserVerificationRequired as exc:
        raise VerificationNeeded(exc, client) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def handle(client: GptMailClient, action) -> Any:
    try:
        result = run(client, action)
    except VerificationNeeded as signal:
        return verification_response(signal.client, signal.exc)
    return response_payload(client=client, result=result)


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
        "version": "0.2.0",
        "auth_enabled": bool(str(os.getenv("API_BEARER_TOKEN") or "").strip()),
        "turnstile_sitekey": turnstile_sitekey(),
        "browser_verification": (
            "GPTMail requires a verified browser session. Render a Turnstile widget "
            "(action 'inbox_browser_verification') and POST the token to /api/verify-browser, "
            "or send a gm_browser_verified cookie in 'cookies'/'state'."
        ),
        "endpoints": [
            "/health",
            "/api/verify-browser",
            "/api/refresh-auth",
            "/api/generate",
            "/api/list",
            "/api/email",
            "/api/clear",
        ],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.post("/api/verify-browser")
def verify_browser(request: BaseRequest, authorization: str | None = Header(default=None)) -> Any:
    require_api_bearer(authorization)
    client = build_client(request)
    return handle(client, lambda: {"verified": True, "raw": client.verify_browser(request.turnstile_token)})


@app.post("/api/refresh-auth")
def refresh_auth(request: EmailRequest, authorization: str | None = Header(default=None)) -> Any:
    require_api_bearer(authorization)
    client = build_client(request)
    return handle(client, lambda: client.refresh_auth(request.email or None))


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
