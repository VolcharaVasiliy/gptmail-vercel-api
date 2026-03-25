from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse


def pick_messages(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    direct = payload.get("emails") or payload.get("messages")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]

    nested = payload.get("data") or payload.get("result") or {}
    if isinstance(nested, dict):
        candidate = nested.get("emails") or nested.get("messages")
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]

    return []


def pick_text_parts(message: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key in (
        "html",
        "text",
        "content",
        "body",
        "message",
        "content_html",
        "content_text",
        "plain_text",
    ):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return parts


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(html.unescape(value.strip()))


_URL_RE = re.compile(r"https?://[^\s<>'\"]+")
_CODE_RE = re.compile(r"\b([A-Z0-9]{4,10}|\d{4,10})\b")


def extract_links(message: dict[str, Any], *, ignore_mail_domain: bool = True) -> list[str]:
    links: list[str] = []
    parser = _HrefParser()

    for part in pick_text_parts(message):
        if "<a" in part.lower():
            parser.feed(part)
        links.extend(_URL_RE.findall(html.unescape(part)))

    unique: list[str] = []
    seen: set[str] = set()
    for link in parser.hrefs + links:
        parsed = urlparse(link)
        if not parsed.scheme or not parsed.netloc:
            continue
        if ignore_mail_domain and parsed.netloc.endswith("mail.chatgpt.org.uk"):
            if parsed.path.startswith("/api/") or parsed.path.startswith("/ru/") or parsed.path.startswith("/en/"):
                continue
        if link not in seen:
            seen.add(link)
            unique.append(link)
    return unique


def extract_latest_code(message: dict[str, Any]) -> str | None:
    parts = [message.get("subject", "")] + pick_text_parts(message)
    for part in parts:
        if not isinstance(part, str):
            continue
        matches = _CODE_RE.findall(html.unescape(part))
        if matches:
            return matches[0]
    return None
