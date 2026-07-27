# Splitwise Analytics Extension — Design

**Date:** 2026-07-28
**Status:** Approved (design), pending spec review
**Author:** Uday + Claude

## Context

The Splitwise MCP server (a fork of `tarunn2799/splitwise-mcp`) exposes 27 read/write tools
but no analytics. The user currently tallies grocery expenses by hand ("Total / Mine / Split")
and, ~1.5 years ago, wrote a pandas script that ingested Splitwise's **CSV export** to produce
per-member spend, category breakdowns, a category×member matrix, and category drill-downs.

That script has two structural problems this design fixes:
1. **CSV reverse-engineering.** It inferred each person's share as
   `personal_expense = Total Cost − Σ(others' negative balances)`, which broke on unequal splits
   and forced a hardcoded `'Flight to'` special case. The **API returns `owed_share` per user
   directly** — the exact personal share, no inference, no special cases.
2. **Manual export step.** It required downloading `Splitwise_expenses.csv` first. The MCP fetches live.

**Goal:** add deterministic analytics tools to the MCP that target `me` / a `group` / a `friend`,
optionally filtered by date range, returning exact structured metrics AND rendering a
self-contained HTML dashboard with charts. No LLM math anywhere.

## Hard constraints (from the user)

- **Deterministic.** All numbers computed in pure Python and returned as structured JSON. The LLM
  never estimates, rounds, or infers a figure — it calls the tool and relays the result verbatim.
  Same input → byte-identical output.
- **UI in scope.** Ship an HTML dashboard, not just text.
- **Offline + zero new heavy deps.** Charts are **inline SVG** (no Chart.js/CDN). Compute is
  **pure stdlib** (`collections`, `datetime`, `decimal`) — no pandas. `Decimal` for money math so
  reconciliation is exact (no float drift).

## Outputs

- **A — in-chat summary:** key metrics as a compact table/JSON returned by the tool, for quick answers.
- **C — HTML dashboard:** one self-contained `.html` file written to disk (path returned), works
  offline, dark-mode, inline-SVG charts + tables. Rendered from the *same* computed metrics as A.

## Architecture

Three new, isolated modules under `src/splitwise_mcp_server/`:

### 0. Rate-limit-aware fetching (design constraint)
Splitwise's Self-Serve API has **unpublished, "conservative" rate limits** (no documented numeric
cap; 429 = too many requests). The existing client already: raises typed `RateLimitError` with
`retry_after` on 429, and auto-retries 500/502/503 once. `_fetch_all_expenses` builds on this:
- Pages fetched **sequentially** (never parallel bursts — bursts are what trip 429s).
- Honors `retry_after` on 429 (existing client behavior); optional small inter-page sleep.
- **`max_pages` safety cap (default generous, e.g. 50 = 5000 expenses).** If hit, the result carries
  `truncated: true` with pages fetched — **logged, never silent** (silent truncation would read as
  "analyzed everything" when it didn't).

### 1. `analytics.py` — pure compute (no I/O, fully unit-testable)
- **Input:** `expenses: list[dict]` (raw API expense objects), `current_user_id: int`,
  `target: TargetSpec` (type = me|group|friend, id), `date_range` (already applied by fetch, used
  for labeling), `group_members` (for member comparison / settlement).
- **Output:** a `AnalyticsResult` dict with these deterministic sections (see Modules below).
- **Money:** every amount parsed to `Decimal`; outputs serialized as strings with 2 decimals to
  preserve exactness across the JSON/HTML boundary.
- **Settlement exclusion:** rows where `payment == true` (Splitwise settle-up payments) are
  excluded from spend analysis, matching the old script's `'paid' / 'Total balance'` filter but
  using the API's structured `payment` flag instead of string matching.
- **Currency guard:** if expenses span multiple `currency_code`s, the result carries a
  `mixed_currency: true` flag and groups totals per currency rather than summing across (the old
  script silently summed — a latent bug). Personal use is INR-only, but the guard prevents wrong
  cross-currency sums.

### 2. `dashboard.py` — render metrics → HTML (deterministic, no network)
- **Input:** the `AnalyticsResult` from `analytics.py`.
- **Output:** an HTML string. A helper writes it to a deterministic path
  `analytics_reports/<target>-<from>-<to>.html` (timestamps come from the data/args, never a clock,
  so re-running same args overwrites the same file).
- **Charts:** hand-rolled inline-SVG helpers (donut for category share, line/bar for monthly trend,
  horizontal bars for member comparison). No JS required to view; a little optional JS only for
  table sort/search (degrades gracefully if disabled).

### 3. New tools in `server.py` — thin wrappers (fetch → compute → return/render)
- **`_fetch_all_expenses(...)`** internal helper: loops `client.get_expenses` over offsets
  (page size 100) until a short page is returned, so no transactions are silently dropped.
- Tools call the helper → `analytics.py` → return JSON; if `generate_dashboard=true`, also call
  `dashboard.py` and include the file path in the response.

## Tools

All accept optional `dated_after` / `dated_before` (ISO 8601, validated by existing
`validate_date_format`) and `generate_dashboard: bool = false`.

### `analyze_spending(target_type, target_id=None, dated_after=None, dated_before=None, generate_dashboard=False)`
- `target_type`: `"me"` | `"group"` | `"friend"`. `target_id` required for group/friend (the group_id
  or friend user_id). For `"me"`, aggregates across everything for the current user.
- **Perspective is always the current (authenticated) user.** For `"friend"`, the scope is the shared
  expenses between you and that friend (`get_expenses(friend_id=...)`), and every metric
  (category, owed-vs-paid-share, ledger) reflects *your* share/paid amounts on those expenses —
  not the friend's independent spending. For `"group"`, category/trend/ledger reflect the group's
  totals, while owed-vs-paid-share is still computed from your perspective; member-comparison (#4)
  and settlement (#7) span all members.
- Returns: category breakdown, monthly trend, owed-vs-paid-share, transaction ledger, top
  transactions, reconciliation. For `group`, also member comparison + settlement (see below).

### `compare_group_members(group_id, dated_after=None, dated_before=None, generate_dashboard=False)`
- Group-only. Returns per-member spend + ranking, category×member matrix, and key insights
  (highest/lowest spender, average, spread) — the old comparison report, live.

(Both tools may render the same dashboard; `analyze_spending` on a group includes the member/
settlement sections so one call can produce the full picture.)

## Analytics modules (all deterministic; all 7 included)

| # | Module | Applies to | In-chat (A) | Dashboard chart (C) |
|---|--------|-----------|-------------|---------------------|
| 1 | **Category breakdown** — amount + % per category | me/group/friend | table | donut |
| 2 | **Monthly trend** — spend per YYYY-MM | all | table | line/bar |
| 3 | **Owed-vs-paid-share** — personal share vs. amount fronted for others ("Mine vs Split") | all | stat tiles | stat tiles |
| 4 | **Per-member comparison** — totals, ranking, category×member matrix | group | table | horizontal bars + matrix |
| 5 | **Transaction ledger** — the rows backing every number | all | table (top N in chat, full in HTML) | sortable/searchable table |
| 6 | **Top transactions** — largest N expenses in the period | all | list | list |
| 7 | **Settlement optimizer** — minimum transactions to settle group balances | group | list | list |

### Settlement optimizer algorithm (deterministic)
Greedy min-cash-flow: from each member's net balance, repeatedly match the largest creditor with
the largest debtor, emit "X pays Y ₹Z", reduce both, until all net ~0. Deterministic tie-break by
`(amount desc, user_id asc)`. Produces ≤ n−1 transactions. This is the net-new feature no existing
Splitwise analytics tool offers.

### Reconciliation (determinism guardrail — from the old script's `validation`)
Every result includes:
- `computed_total` (Σ of the user's owed_share over included, non-payment expenses)
- `api_total` (Σ cost, or Σ owed_share, per the metric)
- `reconciled: bool` (equal within ₹0.01) and `discrepancy` if not.
If not reconciled, the tool surfaces the mismatch in its response rather than returning
numbers that look authoritative but are wrong.

## Data flow

```
tool call (target, dates, generate_dashboard?)
      │
      ▼
_fetch_all_expenses  ── loops get_expenses(offset+=100) until short page ──► list[expense]
      │
      ▼
analytics.compute(expenses, current_user_id, target, members)
      │  (Decimal math, exclude payments, per-currency, reconcile)
      ▼
AnalyticsResult (dict, amounts as 2-dp strings)
      │
      ├─► return JSON  (output A: LLM relays verbatim)
      └─► if generate_dashboard: dashboard.render(result) → write .html → return path (output C)
```

## Error handling

- **Empty result:** if no expenses match, return an explicit `empty: true` with the filters echoed,
  not a zero-filled report that reads as real data.
- **friend/group not found:** surface the API error (existing client behavior); suggest
  `resolve_friend` / `resolve_group` for fuzzy lookup.
- **Mixed currency:** flag and segment per currency (never sum across).
- **Pagination:** if the API errors mid-pagination, fail loudly with how many pages were fetched
  (no partial silent analysis).
- **Dashboard write failure:** return metrics anyway (A still works) with a note that the file
  couldn't be written.

## Testing (`tests/test_analytics.py`)

Pure-function tests with fixture expense payloads (mirroring real API shape):
- Equal split → correct per-person owed_share aggregation.
- **Unequal split** (the ₹1,269-mine grocery case) → owed_share read directly, no `'Flight to'` hack.
- Settlement/payment rows excluded from spend.
- Date filtering boundaries (inclusive edges).
- Multi-currency → `mixed_currency` flag set, no cross-currency sum.
- Category breakdown %s sum to 100 (within rounding).
- Monthly buckets correct across year boundaries.
- Settlement optimizer: balances net to zero; ≤ n−1 transactions; deterministic ordering.
- **Reconciliation** passes on consistent data; flags a deliberately inconsistent fixture.
- Dashboard: `render()` returns valid self-contained HTML containing the computed totals
  (string-presence assertions; no network).

## Out of scope

- Writing analytics back to Splitwise (read-only feature).
- Auth/config changes (reuses existing client + `.env`).
- Real-time/streaming; interactive server-side dashboard (static file only).
- Multi-currency FX conversion (we segment, we don't convert).

## Reuse

- `SplitwiseClient` / `client.get_expenses` (`client.py`) — data source.
- `validate_date_format`, `validate_range` (existing validators) — arg validation.
- Existing `@mcp.tool()` registration pattern in `server.py`.
- The user's old report *structure* (member summary, category matrix, category expansion, validation)
  — reimplemented over `owed_share` instead of CSV inference.
