from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from gptmail_api import DEFAULT_BASE_URL, DEFAULT_LANGUAGE, GptMailClient

app = FastAPI(title="GPTMail Vercel API", version="0.1.0")


class BaseRequest(BaseModel):
    state: dict[str, Any] | None = None
    base_url: str = DEFAULT_BASE_URL
    language: str = DEFAULT_LANGUAGE
    timeout: float = 20.0
    network_attempts: int = Field(default=4, ge=1, le=10)


class GenerateRequest(BaseRequest):
    prefix: str = ""
    domain: str = ""


class EmailRequest(BaseRequest):
    email: str = ""


def require_api_bearer(authorization: str | None) -> None:
    expected = str(os.getenv("API_BEARER_TOKEN") or "").strip()
    if not expected:
        return
    provided = str(authorization or "").strip()
    if provided != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Invalid API bearer token")


def build_client(request: BaseRequest) -> GptMailClient:
    if request.state:
        client = GptMailClient.from_state_payload(
            request.state,
            timeout=request.timeout,
        )
        client.base_url = str(request.base_url or client.base_url).rstrip("/")
        client.language = str(request.language or client.language)
        client.timeout = float(request.timeout)
        client.network_attempts = max(1, int(request.network_attempts))
        return client
    return GptMailClient(
        base_url=request.base_url,
        language=request.language,
        timeout=request.timeout,
        network_attempts=request.network_attempts,
    )


def response_payload(*, client: GptMailClient, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "result": result,
        "state": client.export_state().as_dict(),
    }


def execute_or_raise(action) -> dict[str, Any]:
    try:
        return action()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "gptmail-vercel-api",
        "auth_enabled": bool(str(os.getenv("API_BEARER_TOKEN") or "").strip()),
        "endpoints": ["/health", "/api/refresh-auth", "/api/generate", "/api/list", "/api/clear"],
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.post("/api/refresh-auth")
def refresh_auth(request: EmailRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_api_bearer(authorization)
    client = build_client(request)
    result = execute_or_raise(lambda: client.refresh_auth(request.email or None))
    return response_payload(client=client, result=result)


@app.post("/api/generate")
def generate(request: GenerateRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_api_bearer(authorization)
    client = build_client(request)
    result = execute_or_raise(
        lambda: client.generate_email(
            prefix=request.prefix or None,
            domain=request.domain or None,
        )
    )
    return response_payload(client=client, result=result)


@app.post("/api/list")
def list_emails(request: EmailRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_api_bearer(authorization)
    client = build_client(request)
    result = execute_or_raise(lambda: client.list_emails(request.email or None))
    return response_payload(client=client, result=result)


@app.post("/api/clear")
def clear_emails(request: EmailRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_api_bearer(authorization)
    client = build_client(request)
    result = execute_or_raise(lambda: client.clear_emails(request.email or None))
    return response_payload(client=client, result=result)
