# Gmail Read-Only Connector (Statement Import Half B) — Design

**Date:** 2026-08-05
**Status:** Building

## Context
Half A (statement_import.py) turns parsed transaction rows into a reviewed, deduped,
bulk-created set of expenses. Half B automates the INPUT: read bank/card statement emails
from Gmail (read-only), extract the transaction text, and feed it to Half A — the CRED-style
"no manual entry" flow. CRED itself uses Gmail statement parsing + SMS (researched earlier).

## Prerequisites (USER must do once — cannot be automated)
1. Create a Google Cloud project.
2. Enable the **Gmail API**.
3. Create **OAuth 2.0 Desktop-app credentials**; download `client_secret.json`.
4. Add self as a **test user** on the OAuth consent screen (scope: gmail.readonly).
Place `client_secret.json` at `~/.expensifyai/gmail_client_secret.json`
(or set `EXPENSIFYAI_GMAIL_CLIENT_SECRET`).

## Scope & safety
- **Read-only:** `https://www.googleapis.com/auth/gmail.readonly` only. Never modify/send/delete.
- Token cached at `~/.expensifyai/gmail_token.json` (gitignored; same dir as the DB/splits).
- The connector only *fetches text*; it never creates expenses itself. Extraction of line-items
  from the email body is done by the calling agent (LLM), and creation goes through Half A's
  import_statement → confirm_import (human review gate). So a Gmail read cannot silently write money.

## Module: `gmail_connector.py` (isolated; no Splitwise coupling)
- `get_service(client_secret_path?, token_path?)` — runs the OAuth InstalledAppFlow on first
  use (opens browser), caches/refreshes the token, returns an authorized Gmail service.
  Pure Google-auth; importable without triggering auth (auth happens only when called).
- `search_messages(service, query, max_results=20)` — Gmail search (e.g.
  `from:(alerts@hdfcbank.net) subject:(statement) newer_than:2m`). Returns [{id, snippet, date, from, subject}].
- `get_message_text(service, msg_id)` — fetch a message, decode MIME parts, return plain text
  (strips HTML to text). This is the statement text the agent parses.
- Thin, testable: the Google service is injected, so tests pass a fake service (no network/creds).

## MCP tools (server.py)
- `gmail_find_statements(query?, max_results?)` — default query targets common Indian bank
  statement senders/subjects; returns message list (id/subject/date/snippet) for the agent to pick.
- `gmail_read_statement(message_id)` — returns the decoded text of one statement email.
  Agent then extracts rows and calls import_statement (Half A) → confirm_import.
Both surface a clear error if `client_secret.json` is missing (tells user the setup steps).

## Determinism / boundaries
- The connector is I/O only; no money math, no parsing logic that affects amounts.
- All expense creation still flows through Half A (integer paise, dedup, human review).
- Graceful: missing creds → actionable error, not a crash. Token refresh handled by google-auth.

## Testing (`tests/test_gmail_connector.py`, no network/creds)
- `search_messages` / `get_message_text` against a FAKE Gmail service object (dict-driven),
  verifying query pass-through, id list shape, and MIME text extraction (plain + base64url +
  simple HTML-to-text). Auth flow itself is not unit-tested (needs a browser); it's a thin
  google-auth wrapper documented in the spec.

## Out of scope
- SMS parsing (phone-side; not an MCP capability).
- Auto-categorization of parsed rows beyond what statement_import already does.
- Storing email bodies (only transient fetch).
