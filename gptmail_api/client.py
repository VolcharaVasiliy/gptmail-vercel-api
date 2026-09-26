from __future__ import annotations

import random
import string
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests

DEFAULT_BASE_URL = "https://mail.chatgpt.org.uk"
DEFAULT_LANGUAGE = "ru"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)
# Cloudflare Turnstile sitekey the GPTMail frontend uses for
# "inbox_browser_verification". Override with GPTMAIL_TURNSTILE_SITEKEY.
DEFAULT_TURNSTILE_SITEKEY = "0x4AAAAAAD9zdhyrcm6dCJRt"
REFRESH_BUFFER_SECONDS = 60
VERIFIED_COOKIE = "gm_browser_verified"


class BrowserVerificationRequired(RuntimeError):
    """GPTMail answered 428 browser_verification_required.

    The caller must pass either a fresh Turnstile token (see sitekey) so the
    client can call POST /api/browser-verification, or seed the session with
    a verified cookie (gm_browser_verified) captured from a real browser.
    """

    def __init__(self, message: str = "Browser verification required", sitekey: str = "") -> None:
        super().__init__(message)
        self.sitekey = sitekey


@dataclass
class GptMailClient:
    """Thin direct client for mail.chatgpt.org.uk.

    Same simple shape as the client in zcode-account-hub: one requests
    session, the short-lived inbox token (``token``/``expires_at``, refreshed
    automatically) and GPTMail cookies. The verified-session cookie
    (``gm_browser_verified``) lives ~24 h and travels inside ``state``.
    """

    base_url: str = DEFAULT_BASE_URL
    language: str = DEFAULT_LANGUAGE
    timeout: float = 20.0
    network_attempts: int = 3
    user_agent: str = DEFAULT_USER_AGENT
    turnstile_sitekey: str = DEFAULT_TURNSTILE_SITEKEY
    token: str = ""
    token_email: str = ""
    expires_at: int = 0
    last_email: str = ""
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.network_attempts = max(1, int(self.network_attempts))
        self.session.headers.update(
            {"User-Agent": self.user_agent, "Accept-Language": "en-US,en;q=0.9"}
        )

    # -- state -----------------------------------------------------------------

    @classmethod
    def from_state(cls, payload: dict[str, Any] | None, **overrides: Any) -> "GptMailClient":
        payload = payload or {}
        auth = payload.get("auth") if isinstance(payload.get("auth"), dict) else {}
        kwargs: dict[str, Any] = {
            "base_url": str(payload.get("base_url") or DEFAULT_BASE_URL),
            "language": str(payload.get("language") or DEFAULT_LANGUAGE),
            "timeout": float(payload.get("timeout") or 20.0),
            "network_attempts": int(payload.get("network_attempts") or 3),
            "token": str(auth.get("token") or ""),
            "token_email": str(auth.get("email") or "").strip().lower(),
            "expires_at": int(auth.get("expires_at") or 0),
            "last_email": str(
                payload.get("last_email") or auth.get("email") or ""
            ).strip().lower(),
        }
        kwargs.update(overrides)
        client = cls(**kwargs)
        host = str(urlsplit(client.base_url).hostname or "")
        for cookie in payload.get("cookies") or []:
            if not isinstance(cookie, dict) or not cookie.get("name"):
                continue
            client.session.cookies.set(
                str(cookie["name"]),
                str(cookie.get("value") or ""),
                domain=str(cookie.get("domain") or host),
                path=str(cookie.get("path") or "/"),
            )
        return client

    def export_state(self) -> dict[str, Any]:
        cookies = [
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
                "secure": cookie.secure,
                "expires": cookie.expires,
            }
            for cookie in self.session.cookies
        ]
        return {
            "base_url": self.base_url,
            "language": self.language,
            "network_attempts": self.network_attempts,
            "auth": {
                "token": self.token,
                "email": self.token_email,
                "expires_at": self.expires_at,
            },
            "cookies": cookies,
            "last_email": self.last_email or self.token_email,
            "updated_at": int(time.time()),
        }

    def verified_until(self) -> int:
        """Unix ts when the ~24 h verified-session cookie expires (0 = unknown)."""
        for cookie in self.session.cookies:
            if cookie.name == VERIFIED_COOKIE:
                return int(cookie.expires or 0)
        return 0

    # -- http ------------------------------------------------------------------

    def _mail_referrer(self, email: str | None = None) -> str:
        slug = str(email or self.last_email or self.token_email or "").strip().lower()
        if slug and "--" in slug and "@" not in slug:
            slug = slug.replace("--", "@", 1)
        return f"{self.base_url}/{self.language}/{slug}"

    def _send(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.network_attempts + 1):
            try:
                return self.session.request(method, url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.network_attempts:
                    raise RuntimeError(f"Network error: {exc}") from exc
                time.sleep(min(0.5 * attempt, 2.0))
        raise RuntimeError(f"Network error: {last_error}")

    def _decode(self, response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Invalid JSON response from {response.url}") from exc
        return payload if isinstance(payload, dict) else {}

    def _sync_auth(self, payload: dict[str, Any]) -> None:
        auth = payload.get("auth")
        if isinstance(auth, dict) and auth.get("token"):
            self.token = str(auth["token"])
            self.token_email = str(auth.get("email") or self.token_email).strip().lower()
            self.expires_at = int(auth.get("expires_at") or 0)
            if self.token_email:
                self.last_email = self.last_email or self.token_email

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
        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": self._mail_referrer(email_hint),
            "Origin": self.base_url,
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if require_auth and self.token:
            headers["X-Inbox-Token"] = self.token

        response = self._send(method, url, params=params, json=json_body, headers=headers)
        payload = self._decode(response)

        if response.status_code == 428 and (payload.get("data") or {}).get(
            "code"
        ) == "browser_verification_required":
            raise BrowserVerificationRequired(
                str(payload.get("error") or "Browser verification required"),
                sitekey=self.turnstile_sitekey,
            )

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
            raise RuntimeError(
                payload.get("error") or f"HTTP {response.status_code} for {method} {path}"
            )
        return payload

    def warmup(self) -> None:
        try:
            response = self._send(
                "GET",
                self.base_url + "/",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Warmup failed: {exc}") from exc

    # -- session / protocol ----------------------------------------------------

    def should_refresh(self, email: str | None = None) -> bool:
        normalized = str(email or "").strip().lower()
        if not self.token or self.expires_at - int(time.time()) <= REFRESH_BUFFER_SECONDS:
            return True
        if normalized and self.token_email and normalized != self.token_email:
            return True
        return False

    def refresh_auth(self, email: str | None = None) -> dict[str, Any]:
        """Warmup + POST /api/inbox-token: (re)issue the short-lived inbox token."""
        self.warmup()
        normalized = str(email or self.last_email or self.token_email or "").strip().lower()
        result = self._request(
            "POST",
            "/api/inbox-token",
            json_body={"email": normalized} if normalized else {},
            email_hint=normalized,
            require_auth=False,
            retry_on_auth_error=False,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Failed to refresh auth")
        self._sync_auth(result)
        return result

    def verify_browser(self, turnstile_token: str) -> dict[str, Any]:
        """Exchange a Cloudflare Turnstile token for the ~24 h verified cookie."""
        token = str(turnstile_token or "").strip()
        if not token:
            raise ValueError("Turnstile token is required")
        response = self._send(
            "POST",
            self.base_url + "/api/browser-verification",
            json={"turnstile_token": token},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Referer": self._mail_referrer(),
                "Origin": self.base_url,
            },
        )
        payload = self._decode(response)
        if not response.ok or not payload.get("success"):
            raise RuntimeError(
                payload.get("error")
                or f"Browser verification failed (HTTP {response.status_code})"
            )
        return payload

    def list_domains(self) -> list[str]:
        domains: list[str] = []
        seen: set[str] = set()
        # The site paginates bootstrap domains; these query variants cover it.
        for query in (
            {"view": "bootstrap"},
            {"view": "bootstrap", "page": 2},
            {"view": "bootstrap", "offset": 32},
        ):
            try:
                result = self._request(
                    "GET",
                    "/api/domains/public",
                    params=query,
                    require_auth=False,
                    retry_on_auth_error=False,
                )
            except RuntimeError:
                break
            items = ((result.get("data") or {}).get("domains")) or []
            before = len(seen)
            for item in items:
                if isinstance(item, dict):
                    if item.get("is_active") in (0, False):
                        continue
                    name = str(item.get("domain_name") or "").strip().lower()
                else:
                    name = str(item or "").strip().lower()
                if name and "." in name and name not in seen:
                    seen.add(name)
                    domains.append(name)
            if len(seen) == before:
                break
        return domains

    def generate_email(
        self,
        *,
        prefix: str | None = None,
        domain: str | None = None,
        use_identity: bool = True,
    ) -> dict[str, Any]:
        """Assemble an address (identity username + public domain) and claim it."""
        normalized_prefix = str(prefix or "").strip()
        normalized_domain = str(domain or "").strip().lower()
        identity: dict[str, Any] = {}

        if not normalized_prefix and use_identity:
            try:
                result = self._request(
                    "GET",
                    "/api/generate-identity",
                    require_auth=False,
                    retry_on_auth_error=False,
                )
                identity = result.get("data") if isinstance(result.get("data"), dict) else result
                normalized_prefix = str(identity.get("username") or "").strip()
            except RuntimeError:
                normalized_prefix = ""
        if not normalized_prefix:
            normalized_prefix = (
                "user"
                + "".join(random.choices(string.ascii_lowercase, k=4))
                + "".join(random.choices(string.digits, k=3))
            )
        if not normalized_domain:
            domains = self.list_domains()
            if not domains:
                raise RuntimeError("No public domains available from GPTMail")
            normalized_domain = random.choice(domains)

        email = f"{normalized_prefix}@{normalized_domain}".strip().lower()
        # Claiming the mailbox requires the verified-session cookie; a 428
        # from inbox-token propagates as BrowserVerificationRequired.
        result = self._request(
            "POST",
            "/api/inbox-token",
            json_body={"email": email, "include_emails": True},
            email_hint=email,
            require_auth=False,
            retry_on_auth_error=False,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error") or "Failed to generate email")

        data = result.get("data") or {}
        created = str(data.get("email") or email).strip().lower()
        self.last_email = created
        if not self.token_email:
            self.token_email = created

        return {
            "success": True,
            "email": created,
            "identity": identity,
            "data": data,
            "auth": {
                "token": self.token,
                "email": self.token_email,
                "expires_at": self.expires_at,
            },
        }

    # -- mailbox ---------------------------------------------------------------

    @staticmethod
    def _pick_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        direct = payload.get("emails") or payload.get("messages")
        if not isinstance(direct, list):
            nested = payload.get("data") or {}
            direct = nested.get("emails") if isinstance(nested, dict) else None
        return [m for m in direct or [] if isinstance(m, dict)]

    def _resolved_email(self, email: str | None) -> str:
        resolved = str(email or self.last_email or self.token_email or "").strip().lower()
        if not resolved:
            raise ValueError("Email is required")
        return resolved

    def list_emails(self, email: str | None = None) -> dict[str, Any]:
        resolved = self._resolved_email(email)
        result = self._request(
            "GET", "/api/emails", params={"email": resolved}, email_hint=resolved
        )
        messages = self._pick_messages(result)
        self.last_email = resolved
        return {
            "success": bool(result.get("success", True)),
            "email": resolved,
            "count": len(messages),
            "messages": messages,
            "raw": result,
        }

    def get_email(self, email_id: str, email: str | None = None) -> dict[str, Any]:
        resolved = self._resolved_email(email)
        if not str(email_id or "").strip():
            raise ValueError("Email id is required")
        result = self._request(
            "GET",
            f"/api/email/{str(email_id).strip()}",
            params={"email": resolved, "include_raw": 0},
            email_hint=resolved,
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        return {
            "success": bool(result.get("success", True)),
            "email": resolved,
            "message": data,
            "raw": result,
        }

    def clear_emails(self, email: str | None = None) -> dict[str, Any]:
        resolved = self._resolved_email(email)
        result = self._request(
            "DELETE", "/api/emails/clear", params={"email": resolved}, email_hint=resolved
        )
        self.last_email = resolved
        return result
