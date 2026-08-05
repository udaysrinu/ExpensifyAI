"""Gmail read-only connector — fetch bank/card statement emails to feed statement_import.

Half B of the CRED-style import: this only READS Gmail (scope gmail.readonly) and returns
message text. It never creates expenses — the calling agent extracts line-items from the
text and routes them through statement_import (import_statement -> confirm_import), which
keeps the human-review + dedup + exact-paise guarantees. So a Gmail read cannot silently
write money.

Prerequisites the USER sets up once (see docs spec): a Google Cloud project with the Gmail
API enabled and OAuth 2.0 *Desktop* credentials downloaded to
~/.expensifyai/gmail_client_secret.json. First `get_service()` call opens a browser consent
flow and caches a token at ~/.expensifyai/gmail_token.json.

The Google service is injected into search/read functions, so those are unit-testable with a
fake service (no network, no creds).
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Default query targets common Indian bank/card statement emails; overridable per call.
DEFAULT_STATEMENT_QUERY = (
    'subject:(statement OR "credit card" OR "e-statement" OR bill) '
    'from:(hdfcbank OR icicibank OR axisbank OR sbicard OR onecard OR idfcfirstbank '
    'OR kotak OR amex OR citibank OR hsbc OR rblbank OR yesbank) newer_than:3m'
)


def _client_secret_path(override: Optional[str] = None) -> Path:
    return Path(override or os.getenv("EXPENSIFYAI_GMAIL_CLIENT_SECRET")
                or (Path.home() / ".expensifyai" / "gmail_client_secret.json"))


def _token_path(override: Optional[str] = None) -> Path:
    return Path(override or os.getenv("EXPENSIFYAI_GMAIL_TOKEN")
                or (Path.home() / ".expensifyai" / "gmail_token.json"))


def get_service(client_secret_path: Optional[str] = None, token_path: Optional[str] = None):
    """Return an authorized read-only Gmail service. Runs OAuth on first use, then caches.

    Imports the Google libraries lazily so this module imports cleanly even if they're absent
    or creds aren't set up. Raises a clear, actionable error when the client secret is missing.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "Gmail libraries not installed. Run: pip install google-api-python-client "
            "google-auth-oauthlib google-auth-httplib2"
        ) from e

    tok = _token_path(token_path)
    secret = _client_secret_path(client_secret_path)
    creds = None
    if tok.exists():
        creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secret.exists():
                raise RuntimeError(
                    f"Gmail client secret not found at {secret}. Set up a Google Cloud project, "
                    "enable the Gmail API, create OAuth Desktop credentials, and save the JSON there "
                    "(or set EXPENSIFYAI_GMAIL_CLIENT_SECRET). See the Gmail connector design doc."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
            creds = flow.run_local_server(port=0)
        tok.parent.mkdir(parents=True, exist_ok=True)
        tok.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(headers: List[Dict[str, str]], name: str) -> str:
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def search_messages(service, query: Optional[str] = None, max_results: int = 20) -> List[Dict[str, Any]]:
    """Search Gmail; return [{id, subject, from, date, snippet}] (metadata only, no body)."""
    q = query or DEFAULT_STATEMENT_QUERY
    resp = service.users().messages().list(userId="me", q=q, maxResults=max_results).execute()
    out = []
    for m in resp.get("messages", []):
        full = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["Subject", "From", "Date"]).execute()
        headers = (full.get("payload") or {}).get("headers", [])
        out.append({
            "id": m["id"],
            "subject": _header(headers, "Subject"),
            "from": _header(headers, "From"),
            "date": _header(headers, "Date"),
            "snippet": full.get("snippet", ""),
        })
    return out


def _b64url_decode(data: str) -> str:
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|tr|table|li)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _extract_parts_text(payload: Dict[str, Any]) -> str:
    """Walk a MIME tree, preferring text/plain; fall back to HTML-stripped text."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {}) or {}
    if mime == "text/plain" and body.get("data"):
        return _b64url_decode(body["data"])
    if mime == "text/html" and body.get("data"):
        return _html_to_text(_b64url_decode(body["data"]))
    # multipart: gather children, prefer accumulated plain text
    plain, html = [], []
    for part in payload.get("parts", []) or []:
        t = _extract_parts_text(part)
        if not t:
            continue
        (plain if part.get("mimeType") == "text/plain" else html).append(t)
    if plain:
        return "\n".join(plain).strip()
    return "\n".join(html).strip()


def get_message_text(service, msg_id: str) -> Dict[str, Any]:
    """Fetch one message; return {id, subject, from, date, text} with decoded body text."""
    full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    payload = full.get("payload", {}) or {}
    headers = payload.get("headers", [])
    return {
        "id": msg_id,
        "subject": _header(headers, "Subject"),
        "from": _header(headers, "From"),
        "date": _header(headers, "Date"),
        "text": _extract_parts_text(payload),
    }


def list_attachments(service, msg_id: str) -> List[Dict[str, Any]]:
    """Return [{filename, mime_type, attachment_id, size}] for a message's attachments."""
    full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    out: List[Dict[str, Any]] = []

    def walk(part):
        body = part.get("body", {}) or {}
        if part.get("filename") and body.get("attachmentId"):
            out.append({"filename": part["filename"], "mime_type": part.get("mimeType", ""),
                        "attachment_id": body["attachmentId"], "size": body.get("size", 0)})
        for c in part.get("parts", []) or []:
            walk(c)

    walk(full.get("payload", {}) or {})
    return out


def download_attachment(service, msg_id: str, attachment_id: str) -> bytes:
    """Download one attachment's raw bytes."""
    att = service.users().messages().attachments().get(
        userId="me", messageId=msg_id, id=attachment_id).execute()
    return base64.urlsafe_b64decode(att["data"].encode("utf-8"))


def list_images(service, msg_id: str) -> List[Dict[str, Any]]:
    """Return image parts (inline or attached) for a message.

    Banks sometimes state the statement-PDF password rule in an IMAGE (e.g. RBL). This surfaces
    every image/* part so the caller can fetch it and read the rule via agent vision or OCR
    (password_rule.ocr_image), then feed the transcribed text to parse_password_rule().
    Returns [{filename, mime_type, attachment_id?, data_inline_b64?, size}].
    """
    full = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
    out: List[Dict[str, Any]] = []

    def walk(part):
        mime = part.get("mimeType", "") or ""
        body = part.get("body", {}) or {}
        if mime.startswith("image/"):
            entry = {"filename": part.get("filename", ""), "mime_type": mime,
                     "size": body.get("size", 0)}
            if body.get("attachmentId"):
                entry["attachment_id"] = body["attachmentId"]
            elif body.get("data"):
                entry["data_inline_b64"] = body["data"]  # small inline images ship in-body
            out.append(entry)
        for c in part.get("parts", []) or []:
            walk(c)

    walk(full.get("payload", {}) or {})
    return out


def get_image_bytes(service, msg_id: str, image: Dict[str, Any]) -> bytes:
    """Fetch raw bytes for an image entry from list_images() (handles inline OR attachment)."""
    if image.get("data_inline_b64"):
        return base64.urlsafe_b64decode(image["data_inline_b64"].encode("utf-8"))
    if image.get("attachment_id"):
        return download_attachment(service, msg_id, image["attachment_id"])
    raise ValueError("image entry has neither inline data nor an attachment_id")
