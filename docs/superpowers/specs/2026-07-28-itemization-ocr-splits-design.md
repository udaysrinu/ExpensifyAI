# Itemization + Receipt OCR + Default Splits — Design

**Date:** 2026-07-28
**Status:** Approved (all design decisions resolved), pending build

## Context

ExpensifyAI already covers Splitwise Pro's headline features (charts, search, unlimited
via API). Three Pro-parity gaps remain, and they're interlinked:
- **Structured itemization** — today the user itemizes in free-text notes; there's no
  first-class line-item model feeding the math.
- **Receipt scanning (OCR)** — no way to turn a receipt into an itemized expense.
- **Save default splits** — no reusable split templates (e.g. "Roomies 4-way").

The user's real batches prove why this matters: a single grocery run mixes **per-item
splits** — beers ¾ Ashutosh, party+cake you+Ashu only, groceries 4-way. Today that forces
splitting into multiple Splitwise expenses by hand. Itemization with per-item shares does
it in one expense.

## Resolved design decisions (from brainstorming)

1. **Per-item shares (full power)** — each line-item carries its own split; the tool sums
   each person's per-item owed amounts into their expense-level `owed_share`.
2. **LLM-vision-native OCR** — the calling agent (Claude) reads the receipt image and
   extracts line-items, then calls `create_itemized_expense`. **Zero new dependencies**,
   offline, no OCR engine, no cloud keys. The MCP owns the itemization tool + math, not OCR.
3. **Default splits in a local JSON file** (`~/.expensifyai/splits.json`, overridable).
4. **Determinism** — all money math in integer paise (matches analytics/dashboard),
   reconciled to the expense cost before anything is written to Splitwise.

## Architecture — one new module, tools in server.py

### `itemize.py` — pure compute (no I/O, unit-testable)
Core function `aggregate_items(items, cost=None) -> ItemizedResult`:
- **Input:** `items` — list of line-items, each:
  ```
  {desc, amount, category?, paid_by: user_id,
   split: {type: "equal"|"shares"|"exact", among: [user_ids], shares?: {uid: n}, exact?: {uid: paise}}}
  ```
- **Per-item split resolution** (integer paise, exact):
  - `equal`: split amount across `among`; distribute rounding remainder deterministically
    (largest-remainder, then by user_id) so the item's owed shares sum exactly to its amount.
  - `shares`: proportional to weights; same remainder handling.
  - `exact`: use given paise; validate they sum to the item amount (else ValidationError).
- **Aggregate** across items → per-user `paid` (from each item's `paid_by`) and `owed`
  (sum of per-item owed). Produce the Splitwise `users` array (paid_share/owed_share as
  2-dp strings).
- **Reconciliation:** Σ items == expense cost, and per-user Σ(owed) == cost. If a `cost`
  is passed, assert it equals Σ items. Return `reconciled: bool` + discrepancy; the tool
  refuses to create on mismatch (no silently-wrong expense).
- Also emit a human-readable itemized `details` string (the note we already write by hand).

### Tools in `server.py`
- **`create_itemized_expense(description, group_id, items, currency_code="INR", date?, dry_run=False)`**
  → `aggregate_items` → if reconciled, call `client.create_expense` with the computed
  `users` + itemized `details`; else return the reconciliation error. `dry_run=True`
  returns the computed split without writing (lets the agent preview before committing).
- **`save_default_split(name, split)`** / **`list_default_splits()`** / **`get_default_split(name)`**
  → read/write `~/.expensifyai/splits.json`. A split template stores a reusable
  `split` object (type/among/shares) referenced by name; `create_itemized_expense` items
  may reference `split_ref: "<name>"` which is expanded from the store.

### Receipt OCR flow (no code in the server)
Documented pattern: user shares a receipt image with Claude → Claude extracts line-items
→ calls `create_itemized_expense` (optionally `dry_run` first to confirm) → attaches the
image to the created expense out-of-band (receipt upload remains a separate future gap).

## Determinism & edge cases
- Integer paise throughout; `_paise`/`_money` reused from `analytics.py` conventions.
- Rounding: largest-remainder distribution so equal splits with indivisible paise still
  sum exactly (e.g. ₹100 / 3 = 33.33, 33.33, 33.34).
- Empty items → ValidationError. Item amount ≤ 0 → ValidationError.
- `paid_by` not in `among` is allowed (you pay, others owe).
- Multi-payer supported (each item has one payer; different items can have different payers).
- Currency: single currency per expense (Splitwise constraint); validated.

## Testing (`tests/test_itemize.py`)
- Equal split with indivisible remainder sums exactly to item amount.
- Shares split (beers ¾ Ashu / ¼ Uday) → correct paise.
- Exact split validation (rejects shares that don't sum to amount).
- **The real batch**: mixed items (beers shares, party+cake you+Ashu equal, groceries
  4-way equal) → aggregate matches the hand-computed Groceries+NewYear numbers.
- Reconciliation flags a deliberately inconsistent set.
- Default split save/load round-trip (temp dir).
- `dry_run` returns split without calling the client.

## Out of scope
- OCR engine in the server (LLM-vision does it).
- Receipt image upload (separate known gap).
- Currency conversion (separate feature).

## Reuse
- Paise/money helpers and reconciliation pattern from `analytics.py`.
- `client.create_expense`, existing validators, `@mcp.tool()` pattern in `server.py`.
- The itemized `details` string mirrors the notes we already write by hand.
