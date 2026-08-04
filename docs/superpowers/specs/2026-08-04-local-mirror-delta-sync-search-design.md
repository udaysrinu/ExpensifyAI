# Local Mirror + Delta Sync + Search — Design

**Date:** 2026-08-04
**Status:** Approved (decisions resolved), pending build

## Context
Every lookup this session ("what is 3413", "all Dinesh fuel", "Bir trip total") required live
API calls, pagination (which truncated at 50 twice), and grepping partial JSON. Splitwise's own
search is Pro-paywalled and the API has no search endpoint. Solution: mirror all expenses to a
local SQLite DB via **delta sync**, then search/query locally — instant, offline, covers all 44 groups.

## Resolved decisions
- **SQLite** at `~/.expensifyai/splitwise.db` (override `EXPENSIFYAI_DB_PATH`). FTS5 for text search.
- **Delta sync**, not full reload — use `get_expenses(updated_after=<cursor>)`.
- Scope now: `sync_all` + `search_expenses`. (Analytics-from-mirror is a later follow-up.)

## Delta sync — the core
`updated_after` is the right cursor (NOT `dated_after`): it tracks when a record *changed*, so a
delta pull returns **new + edited + moved + deleted** expenses (edits/deletes bump `updated_at`).
- **First sync:** no cursor → full pull (all groups, paginated 100/page, sequential).
- **Subsequent:** `updated_after = last_synced_at` → only changed rows. Tiny.
- **Upsert by expense `id`** (INSERT OR REPLACE) — idempotent; re-running same delta is safe.
- **Deletes:** rows with `deleted_at != null` are kept but flagged `deleted=1` (excluded from search
  by default; recoverable/visible if asked). Splitwise soft-deletes, so this mirrors faithfully.
- **Cursor stored** in `sync_state`; set to sync-start time AFTER a successful full pass (so a
  mid-sync failure doesn't advance the cursor and skip rows).
- **Groups & friends:** small, no reliable `updated_after` on balances/membership → **full refresh**
  each sync (one `get_groups` + one `get_friends` call). Cheap.

## SQLite schema
```
sync_state(key TEXT PRIMARY KEY, value TEXT)              -- last_synced_at, etc.
groups(id INTEGER PK, name, group_type, updated_at, raw JSON)
friends(id INTEGER PK, first_name, last_name, raw JSON)
expenses(
  id INTEGER PK, group_id, description, details, cost REAL, currency,
  category, date, created_at, updated_at, deleted INTEGER DEFAULT 0,
  payment INTEGER, raw JSON                                -- raw = full API object, source of truth
)
expense_users(expense_id, user_id, paid_share REAL, owed_share REAL,
              PRIMARY KEY(expense_id, user_id))            -- for per-person search
expenses_fts USING fts5(description, details, category, content='expenses', content_rowid='id')
```
Money stored as REAL for query convenience, but `raw` keeps the exact string; search/display uses
`raw` values so no float drift reaches the user (same discipline as analytics).

## Modules
### `mirror.py` — pure SQLite layer (unit-testable with temp db, no network)
- `connect(path)` / `init_schema()`
- `upsert_expense(raw)` — parse raw API expense → expenses + expense_users + FTS; set deleted flag.
- `upsert_group(raw)`, `upsert_friend(raw)`
- `get_cursor()` / `set_cursor(ts)`
- `search(text=None, min_amount=None, max_amount=None, user_id=None, group_id=None,
          category=None, dated_after=None, dated_before=None, include_deleted=False,
          limit=50)` → rows (from `raw`, so faithful).
- `stats()` — counts for verification.

### server.py tools
- **`sync_all(full=False)`** — fetch groups+friends (full), then `_fetch_all_expenses(updated_after=cursor)`
  paginated sequentially; upsert each; advance cursor on success. Returns {synced, new, updated,
  deleted, total_in_db, elapsed}. `full=True` ignores cursor (force full resync).
- **`search_expenses(query?, filters…)`** — thin wrapper over `mirror.search`; returns matches.
  Resolves person/group names to ids via existing resolvers if a name is passed.

## Determinism / edge cases
- Sequential pagination + honor 429 (existing client behavior) — never bursts.
- Cursor advances only after a clean pass (crash-safe; no skipped rows).
- Multi-currency: stored per-row; search can filter by currency.
- FTS rebuild kept in sync via triggers or explicit re-index on upsert.
- Empty DB / first run clearly reported ("full sync, N expenses").

## Testing (`tests/test_mirror.py`, pure, temp db)
- upsert insert then re-upsert same id → 1 row, updated fields (no dup).
- deleted_at set → row flagged deleted, excluded from default search.
- search by text (FTS), by amount range, by user_id, by group, by category, by date.
- cursor get/set round-trip.
- expense_users populated; per-person search returns correct rows.
- include_deleted toggle.

## Out of scope (now)
- Rewiring analytics to read the mirror (later).
- Real-time/push sync (manual `sync_all` only).
- Writing back to Splitwise from the mirror (read-only mirror).

## Reuse
- `client.get_expenses(updated_after=…)`, `get_groups`, `get_friends`, `_fetch_all_expenses` pattern.
- Resolver tools for name→id in search.
