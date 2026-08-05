"""Gmail connector tests — no network, no creds. A fake Gmail service is injected.

The OAuth flow in get_service() is a thin google-auth wrapper (needs a browser) and is not
unit-tested; the parsing/search logic — the part with real behavior — is fully covered here.
"""

import base64
import pytest
from splitwise_mcp_server import gmail_connector as gc


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


class FakeExec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        return self._r


class FakeMessages:
    def __init__(self, store):
        self.store = store           # {msg_id: full_message}
        self.list_result = {"messages": [{"id": i} for i in store]}
        self.last_query = None

    def list(self, userId, q, maxResults):
        self.last_query = q
        return FakeExec(self.list_result)

    def get(self, userId, id, format, metadataHeaders=None):
        return FakeExec(self.store[id])


class FakeUsers:
    def __init__(self, store):
        self._m = FakeMessages(store)

    def messages(self):
        return self._m


class FakeService:
    def __init__(self, store):
        self._u = FakeUsers(store)

    def users(self):
        return self._u


def _msg(subject, frm, plain=None, html=None):
    payload = {"headers": [{"name": "Subject", "value": subject},
                           {"name": "From", "value": frm},
                           {"name": "Date", "value": "Wed, 01 Jul 2026 10:00:00 +0530"}],
               "mimeType": "multipart/alternative", "parts": []}
    if plain is not None:
        payload["parts"].append({"mimeType": "text/plain", "body": {"data": b64(plain)}})
    if html is not None:
        payload["parts"].append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {"snippet": (plain or html or "")[:40], "payload": payload}


def test_search_passes_query_and_returns_metadata():
    store = {"m1": _msg("Your HDFC Statement", "alerts@hdfcbank.net", plain="hi")}
    svc = FakeService(store)
    res = gc.search_messages(svc, query="subject:statement", max_results=5)
    assert svc.users().messages().last_query == "subject:statement"
    assert res[0]["id"] == "m1"
    assert res[0]["subject"] == "Your HDFC Statement"
    assert res[0]["from"] == "alerts@hdfcbank.net"


def test_default_query_used_when_none():
    svc = FakeService({"m1": _msg("s", "f", plain="x")})
    gc.search_messages(svc, query=None)
    assert svc.users().messages().last_query == gc.DEFAULT_STATEMENT_QUERY


def test_get_message_text_prefers_plain():
    store = {"m1": _msg("Stmt", "bank", plain="KFC 497\nZepto 1650", html="<p>ignore me</p>")}
    out = gc.get_message_text(FakeService(store), "m1")
    assert "KFC 497" in out["text"] and "Zepto 1650" in out["text"]
    assert "ignore me" not in out["text"]        # plain preferred over html
    assert out["subject"] == "Stmt"


def test_get_message_text_html_fallback():
    store = {"m1": _msg("Stmt", "bank", html="<table><tr><td>Fuel</td><td>2000</td></tr></table>")}
    out = gc.get_message_text(FakeService(store), "m1")
    assert "Fuel" in out["text"] and "2000" in out["text"]
    assert "<td>" not in out["text"]              # tags stripped


def test_html_to_text_strips_scripts_and_tags():
    html = "<style>x{}</style><script>bad()</script><p>Amount 5000</p>"
    t = gc._html_to_text(html)
    assert "Amount 5000" in t and "bad()" not in t and "x{}" not in t


def test_header_lookup_case_insensitive():
    headers = [{"name": "subject", "value": "hello"}]
    assert gc._header(headers, "Subject") == "hello"
    assert gc._header(headers, "Missing") == ""


def test_get_service_missing_secret_raises_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPENSIFYAI_GMAIL_TOKEN", str(tmp_path / "tok.json"))
    monkeypatch.setenv("EXPENSIFYAI_GMAIL_CLIENT_SECRET", str(tmp_path / "nope.json"))
    with pytest.raises(RuntimeError, match="client secret not found"):
        gc.get_service()
