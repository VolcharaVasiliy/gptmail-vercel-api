from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests

from .parsers import pick_messages

DEFAULT_BASE_URL = "https://mail.chatgpt.org.uk"
DEFAULT_LANGUAGE = "ru"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
REFRESH_BUFFER_SECONDS = 60


@dataclass
class AuthState:
    token: str = ""
    email: str = ""
    expires_at: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "AuthState":
        payload = payload or {}
        return cls(
            token=str(payload.get("token") or ""),
            email=str(payload.get("email") or "").strip().lower(),
            expires_at=int(payload.get("expires_at") or payload.get("expiresAt") or 0),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "email": self.email,
            "expires_at": self.expires_at,
        }


@dataclass
class SessionState:
    base_url: str
    language: str
    network_attempts: int = 3
    auth: AuthState = field(default_factory=AuthState)
    cookies: list[dict[str, Any]] = field(default_factory=list)
    last_email: str = ""
    updated_at: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "language": self.language,
            "network_attempts": self.network_attempts,
            "auth": self.auth.as_dict(),
            "cookies": self.cookies,
            "last_email": self.last_email,
            "updated_at": self.updated_at,
        }


class GptMailClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        language: str = DEFAULT_LANGUAGE,
        timeout: float = 20.0,
        network_attempts: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.timeout = timeout
        self.network_attempts = max(1, int(network_attempts))
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.auth = AuthState()
        self.last_email = ""

        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    @classmethod
    def from_state_payload(
        cls,
        payload: dict[str, Any] | None,
        *,
        timeout: float = 20.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> "GptMailClient":
        payload = payload or {}
        client = cls(
            base_url=str(payload.get("base_url") or DEFAULT_BASE_URL),
            language=str(payload.get("language") or DEFAULT_LANGUAGE),
            timeout=float(payload.get("timeout") or timeout),
            network_attempts=int(payload.get("network_attempts") or 3),
            user_agent=user_agent,
        )
        client.auth = AuthState.from_payload(payload.get("auth"))
        client.last_email = str(payload.get("last_email") or client.auth.email or "").strip().lower()

        for cookie in payload.get("cookies") or []:
            if not isinstance(cookie, dict) or not cookie.get("name"):
                continue
            created = requests.cookies.create_cookie(
                name=str(cookie["name"]),
                value=str(cookie.get("value") or ""),
                domain=str(cookie.get("domain") or urlsplit(client.base_url).hostname or ""),
                path=str(cookie.get("path") or "/"),
                secure=bool(cookie.get("secure", True)),
                expires=cookie.get("expires"),
            )
            client.session.cookies.set_cookie(created)

        return client

    def export_state(self) -> SessionState:
        cookies: list[dict[str, Any]] = []
        for cookie in self.session.cookies:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                }
            )
        return SessionState(
            base_url=self.base_url,
            language=self.language,
            network_attempts=self.network_attempts,
            auth=self.auth,
            cookies=cookies,
            last_email=self.last_email or self.auth.email,
            updated_at=int(time.time()),
        )

    def _mail_slug(self, email_or_slug: str | None) -> str:
        raw = str(email_or_slug or "").strip().lower()
        if not raw:
            return ""
        if "@" in raw:
            local_part, domain = raw.split("@", 1)
            return f"{local_part}--{domain}"
        return raw

    def _mail_referrer(self, email_or_slug: str | None = None) -> str:
        slug = self._mail_slug(email_or_slug)
        if slug:
            return f"{self.base_url}/{self.language}/{slug}"
        return f"{self.base_url}/{self.language}/"

    def _should_refresh(self, email: str | None = None) -> bool:
        normalized = str(email or "").strip().lower()
        now = int(time.time())
        if not self.auth.token:
            return True
        if self.auth.expires_at - now <= REFRESH_BUFFER_SECONDS:
            return True
        if normalized and self.auth.email and normalized != self.auth.email:
            return True
        return False

    def _sync_auth(self, payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        auth = payload.get("auth")
        if isinstance(auth, dict):
            self.auth = AuthState.from_payload(auth)
            self.last_email = self.auth.email or self.last_email

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        email_hint: str | None = None,
        require_auth: bool = True,
        retry_on_auth_error: bool = True,
    ) -> dict[str, Any]:
        if require_auth and self._should_refresh(email_hint):
            self.refresh_auth(email_hint)

        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": self._mail_referrer(email_hint or self.auth.email or self.last_email),
            "Origin": self.base_url,
            "Connection": "close",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if require_auth and self.auth.token:
            headers["X-Inbox-Token"] = self.auth.token

        response = self._send_request(
            method=method.upper(),
            url=url,
            params=params,
            json=json_body,
            headers=headers,
        )

        payload = self._decode_json(response)
        self._sync_auth(payload)

        if require_auth and retry_on_auth_error and response.status_code in (401, 403):
            self.refresh_auth(email_hint)
            return self._request(
                method,
                path,
                params=params,
                json_body=json_body,
                email_hint=email_hint,
                require_auth=require_auth,
                retry_on_auth_error=False,
            )

        if not response.ok:
            message = payload.get("error") if isinstance(payload, dict) else None
            raise RuntimeError(message or f"HTTP {response.status_code} for {method} {path}")

        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected response type for {method} {path}")

        return payload

    @staticmethod
    def _decode_json(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response from {response.url}") from exc
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(f"Unexpected JSON shape from {response.url}")

    def _send_request(self, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.network_attempts + 1):
            try:
                return self.session.request(timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.network_attempts:
                    raise RuntimeError(f"Network error: {exc}") from exc
                time.sleep(min(0.5 * attempt, 2.0))
        raise RuntimeError(f"Network error: {last_error}")

    def warmup(self) -> None:
        response = self._send_request(
            method="GET",
            url=self.base_url + "/",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Connection": "close",
            },
        )
        response.raise_for_status()

    def refresh_auth(self, email: str | None = None) -> dict[str, Any]:
        self.warmup()
        payload: dict[str, Any] = {}
        normalized = str(email or self.auth.email or self.last_email or "").strip().lower()
        if normalized:
            payload["email"] = normalized
        result = self._request(
            "POST",
            "/api/inbox-token",
            json_body=payload,
            email_hint=normalized,
            require_auth=False,
            retry_on_auth_error=False,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Failed to refresh auth")
        self._sync_auth(result)
        return result

    def generate_email(self, *, prefix: str | None = None, domain: str | None = None) -> dict[str, Any]:
        if not self.auth.token:
            self.refresh_auth()
        normalized_prefix = str(prefix or "").strip()
        normalized_domain = str(domain or "").strip().lower()
        payload: dict[str, Any] | None = None
        if normalized_prefix or normalized_domain:
            payload = {}
            if normalized_prefix:
                payload["prefix"] = normalized_prefix
            if normalized_domain:
                payload["domain"] = normalized_domain
        method = "POST" if payload else "GET"
        result = self._request(
            method,
            "/api/generate-email",
            json_body=payload,
            email_hint=self.auth.email or self.last_email,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Failed to generate email")

        data = result.get("data") or {}
        email = str(data.get("email") or result.get("email") or "").strip().lower()
        if not email:
            raise RuntimeError("API did not return an email address")

        self.last_email = email
        if not self.auth.email:
            self.auth.email = email
        return {
            "success": True,
            "email": email,
            "data": data,
            "auth": self.auth.as_dict(),
        }

    def list_emails(self, email: str | None = None) -> dict[str, Any]:
        resolved = str(email or self.last_email or self.auth.email or "").strip().lower()
        if not resolved:
            raise ValueError("Email is required for list_emails")
        result = self._request(
            "GET",
            "/api/emails",
            params={"email": resolved},
            email_hint=resolved,
        )
        messages = pick_messages(result)
        self.last_email = resolved
        return {
            "success": bool(result.get("success", True)),
            "email": resolved,
            "count": len(messages),
            "messages": messages,
            "raw": result,
        }

    def clear_emails(self, email: str | None = None) -> dict[str, Any]:
        resolved = str(email or self.last_email or self.auth.email or "").strip().lower()
        if not resolved:
            raise ValueError("Email is required for clear_emails")
        result = self._request(
            "DELETE",
            "/api/emails/clear",
            params={"email": resolved},
            email_hint=resolved,
        )
        self.last_email = resolved
        return result

    @staticmethod
    def export_state_json(state: dict[str, Any]) -> str:
        return json.dumps(state, ensure_ascii=False, separators=(",", ":"))
