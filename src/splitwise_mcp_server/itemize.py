"""Itemized-expense math: aggregate per-item splits into an exact per-user split.

Pure compute, integer paise, deterministic. Each line-item on a receipt can have its
OWN split (beers ¾ one person, groceries 4-way, cake between two) — this module resolves
each item's split exactly, then sums per-user owed/paid across items into the arrays
Splitwise's create_expense expects. Rounding uses largest-remainder distribution so an
indivisible amount (₹100 / 3) still sums back to the exact item total.

No network, no clock. `aggregate_items` is the entry point; the server tool calls it,
checks `reconciled`, and only then writes to Splitwise.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

TWO = Decimal("0.01")


class ItemizeError(ValueError):
    """Raised when line-items are malformed or a split can't reconcile."""


# --- paise helpers (integer = exact) ---------------------------------------

def _to_paise(v: Any) -> int:
    """Parse a rupee amount (str/int/float/Decimal) to integer paise."""
    if v is None or v == "":
        raise ItemizeError("amount is required")
    try:
        d = Decimal(str(v))
    except Exception as e:
        raise ItemizeError(f"invalid amount: {v!r}") from e
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _rupees(paise: int) -> str:
    """Integer paise -> 2-dp rupee string for the Splitwise API."""
    neg = paise < 0
    p = abs(paise)
    return f"{'-' if neg else ''}{p // 100}.{p % 100:02d}"


def _largest_remainder(total_paise: int, weights: List[int]) -> List[int]:
    """Split total_paise across len(weights) parts proportional to weights, exactly.

    Floor each share, then hand the leftover paise one-by-one to the parts with the
    largest fractional remainder (ties broken by original index — deterministic).
    Sum of the result always equals total_paise.
    """
    wsum = sum(weights)
    if wsum <= 0:
        raise ItemizeError("split weights must sum to a positive value")
    floors, rema = [], []
    for w in weights:
        exact = total_paise * w  # scaled by wsum
        fl = exact // wsum
        floors.append(fl)
        rema.append(exact - fl * wsum)  # remainder numerator, 0..wsum-1
    leftover = total_paise - sum(floors)
    # distribute leftover paise to largest remainders (stable by index on ties)
    order = sorted(range(len(weights)), key=lambda i: (-rema[i], i))
    for k in range(leftover):
        floors[order[k]] += 1
    return floors


# --- per-item split resolution ---------------------------------------------

def _resolve_item_split(item: Dict[str, Any], amount_paise: int) -> Dict[int, int]:
    """Return {user_id: owed_paise} for one item, summing exactly to amount_paise."""
    split = item.get("split") or {}
    stype = split.get("type", "equal")
    among = split.get("among") or []

    if stype == "exact":
        exact = split.get("exact") or {}
        if not exact:
            raise ItemizeError(f"exact split needs an 'exact' map: {item.get('desc')!r}")
        owed = {int(uid): _to_paise(v) for uid, v in exact.items()}
        if sum(owed.values()) != amount_paise:
            raise ItemizeError(
                f"exact shares for {item.get('desc')!r} sum to "
                f"{_rupees(sum(owed.values()))}, not the item amount {_rupees(amount_paise)}"
            )
        return owed

    if not among:
        raise ItemizeError(f"split needs 'among' user ids: {item.get('desc')!r}")
    among = [int(u) for u in among]

    if stype == "equal":
        weights = [1] * len(among)
    elif stype == "shares":
        sh = split.get("shares") or {}
        if not sh:
            raise ItemizeError(f"shares split needs a 'shares' map: {item.get('desc')!r}")
        weights = [int(sh.get(str(u), sh.get(u, 0))) for u in among]
        if sum(weights) <= 0:
            raise ItemizeError(f"shares must be positive: {item.get('desc')!r}")
    else:
        raise ItemizeError(f"unknown split type {stype!r}")

    parts = _largest_remainder(amount_paise, weights)
    return {u: parts[i] for i, u in enumerate(among)}


# --- aggregation ------------------------------------------------------------

def aggregate_items(
    items: List[Dict[str, Any]],
    cost: Optional[Any] = None,
    default_splits: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Aggregate per-item splits into an expense-level split.

    items: each {desc, amount, category?, paid_by, split | split_ref}.
      split_ref names a saved template in default_splits (expanded here).
    cost: optional expected expense total (rupees); validated == Σ items.

    Returns a dict with:
      total_paise, cost (str), users (Splitwise array with paid_share/owed_share),
      details (itemized human string), reconciled (bool), discrepancy (str),
      per_user (uid -> {paid, owed}).
    """
    if not items:
        raise ItemizeError("at least one item is required")
    default_splits = default_splits or {}

    paid: Dict[int, int] = {}
    owed: Dict[int, int] = {}
    total = 0
    lines: List[str] = []

    for it in items:
        desc = it.get("desc") or it.get("description") or "item"
        amt = _to_paise(it.get("amount"))
        if amt <= 0:
            raise ItemizeError(f"item amount must be positive: {desc!r}")
        payer = it.get("paid_by")
        if payer is None:
            raise ItemizeError(f"item needs paid_by: {desc!r}")
        payer = int(payer)

        # expand split_ref from saved templates
        if "split" not in it and it.get("split_ref"):
            ref = default_splits.get(it["split_ref"])
            if not ref:
                raise ItemizeError(f"unknown split_ref {it['split_ref']!r}")
            it = {**it, "split": ref}

        item_owed = _resolve_item_split(it, amt)

        total += amt
        paid[payer] = paid.get(payer, 0) + amt
        for uid, p in item_owed.items():
            owed[uid] = owed.get(uid, 0) + p

        cat = it.get("category")
        lines.append(f"{_rupees(amt)} - {desc}" + (f" [{cat}]" if cat else ""))

    # reconciliation: per-user owed must sum to total; optional cost check
    owed_sum = sum(owed.values())
    reconciled = owed_sum == total
    if cost is not None:
        cost_paise = _to_paise(cost)
        if cost_paise != total:
            reconciled = False
    discrepancy = _rupees(total - owed_sum)

    # build Splitwise users array (union of payers and owers)
    uids = sorted(set(paid) | set(owed))
    users = [{
        "user_id": u,
        "paid_share": _rupees(paid.get(u, 0)),
        "owed_share": _rupees(owed.get(u, 0)),
    } for u in uids]

    details = "\n".join(lines) + f"\n\nTotal {_rupees(total)} · {len(items)} items"

    return {
        "total_paise": total,
        "cost": _rupees(total),
        "users": users,
        "per_user": {u: {"paid": paid.get(u, 0), "owed": owed.get(u, 0)} for u in uids},
        "details": details,
        "reconciled": reconciled,
        "discrepancy": discrepancy,
    }
