"""Statement import — turn a parsed bank/card statement into proposed Splitwise expenses.

The calling agent (Claude) reads the raw statement (PDF/CSV/email/screenshot) and passes
clean transaction rows here. This module does the DETERMINISTIC part:
  1. normalize amounts to integer paise (exact),
  2. propose a category (keyword rules; the agent can override),
  3. propose a split (a named default-split template, or 100%-personal),
  4. flag likely DUPLICATES against already-known expenses (so re-importing a statement
     doesn't double-add rows you already entered),
so the agent can present one review table. After the user approves, the server tool
bulk-creates the confirmed rows via the itemization engine.

Pure compute: no network. `build_proposal` is the entry point.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

# merchant keyword -> Splitwise category name (best-effort; agent may override per row)
_CATEGORY_RULES = [
    (("swiggy", "zomato", "restaurant", "cafe", "kfc", "dominos", "pizza", "biryani",
      "food", "eatery", "dhaba", "hotel "), "Dining out"),
    (("bigbasket", "zepto", "blinkit", "instamart", "grocery", "dmart", "supermarket"), "Groceries"),
    (("petrol", "fuel", "hpcl", "iocl", "bpcl", "shell", "indian oil", "diesel"), "Gas/fuel"),
    (("bookmyshow", "pvr", "inox", "cinema", "movie", "netflix", "spotify", "prime"), "Entertainment"),
    (("uber", "ola", "rapido", "taxi", "cab", "metro", "irctc", "railway"), "Transportation"),
    (("electricity", "power", "bescom", "tneb", "recharge", "airtel", "jio", "broadband"), "Utilities"),
    (("amazon", "flipkart", "myntra", "shopping"), "General"),
    (("pharmacy", "apollo", "medplus", "hospital", "clinic", "chemist"), "Medical"),
]


def _paise(v: Any) -> int:
    if v is None or v == "":
        raise ValueError("amount required")
    d = Decimal(str(v).replace(",", "").replace("₹", "").strip())
    return int((d.copy_abs() * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _rupees(p: int) -> str:
    return f"{p // 100}.{p % 100:02d}"


def suggest_category(merchant: str) -> str:
    m = (merchant or "").lower()
    for keys, cat in _CATEGORY_RULES:
        if any(k in m for k in keys):
            return cat
    return "General"


def _key(date10: str, paise: int) -> str:
    return f"{date10}|{paise}"


def build_proposal(
    transactions: List[Dict[str, Any]],
    current_user_id: int,
    default_split_name: Optional[str] = None,
    default_splits: Optional[Dict[str, Any]] = None,
    existing: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Turn parsed statement rows into a reviewable proposal.

    transactions: [{date: 'YYYY-MM-DD', merchant/description, amount, category?, split_ref?}]
    default_split_name: template applied to rows without their own split_ref (else 100%-personal).
    default_splits: the saved-templates map (from splits_store) to validate split_ref names.
    existing: known expenses (from the local mirror) to flag duplicates against —
              each needs {date, cost}. Match = same day (YYYY-MM-DD) AND same paise amount.

    Returns {proposals: [...], summary: {...}} — nothing is created here.
    """
    default_splits = default_splits or {}
    # index existing by (day, paise) for O(1) duplicate detection
    seen = set()
    for e in (existing or []):
        try:
            d = (e.get("date") or "")[:10]
            seen.add(_key(d, _paise(e.get("cost"))))
        except Exception:
            continue

    proposals = []
    total_paise = dup_count = 0
    for i, tx in enumerate(transactions):
        desc = tx.get("merchant") or tx.get("description") or "Transaction"
        try:
            paise = _paise(tx.get("amount"))
        except Exception as e:
            proposals.append({"index": i, "error": f"bad amount: {tx.get('amount')!r}", "raw": tx})
            continue
        day = (tx.get("date") or "")[:10]
        total_paise += paise

        category = tx.get("category") or suggest_category(desc)
        split_ref = tx.get("split_ref") or default_split_name
        if split_ref and split_ref not in default_splits:
            split_ref = None  # unknown template -> fall back to personal
        split_desc = f"template '{split_ref}'" if split_ref else "100% you (personal)"

        is_dup = _key(day, paise) in seen
        if is_dup:
            dup_count += 1

        proposals.append({
            "index": i,
            "date": day,
            "description": desc,
            "amount": _rupees(paise),
            "category": category,
            "split": split_desc,
            "split_ref": split_ref,
            "duplicate_of_existing": is_dup,
            "include": not is_dup,   # default: skip likely dups; user can flip
        })

    return {
        "proposals": proposals,
        "summary": {
            "count": len(transactions),
            "total": _rupees(total_paise),
            "likely_duplicates": dup_count,
            "will_import": sum(1 for p in proposals if p.get("include")),
            "default_split": default_split_name or "personal",
        },
    }
