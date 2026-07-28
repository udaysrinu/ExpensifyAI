"""Deterministic analytics over Splitwise expenses.

Pure compute: no network, no clock, no randomness. Same input -> byte-identical
output. All money math uses Decimal (quantized to 2 dp, ROUND_HALF_UP) so totals
reconcile exactly against the API's reported figures — no float drift.

The public entry point is `compute_analytics(...)`, which returns a JSON-serializable
dict with amounts rendered as 2-decimal strings. `dashboard.py` renders that dict;
`server.py` fetches the expenses and calls this. Nothing here touches I/O, so it is
fully unit-testable with fixture payloads.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")


# ----------------------------------------------------------------------------
# Money helpers
# ----------------------------------------------------------------------------

def _money(value: Any) -> Decimal:
    """Parse an API amount (string like "520.5", int, float, or None) to Decimal.

    Floats are routed through str() so that e.g. 0.1 does not acquire binary noise.
    None / "" / unparseable -> 0.
    """
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def _q(value: Decimal) -> Decimal:
    """Quantize to 2 decimal places, half-up."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _s(value: Decimal) -> str:
    """Serialize a Decimal to a 2-dp string for JSON/HTML output."""
    return str(_q(value))


def _pct(part: Decimal, whole: Decimal) -> str:
    """Percentage of part/whole as a 1-dp string; 0 when whole is 0."""
    if whole == ZERO:
        return "0.0"
    return str((part / whole * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


# ----------------------------------------------------------------------------
# Normalization
# ----------------------------------------------------------------------------

class NormalizedExpense:
    """A single non-payment, non-deleted expense reduced to what analytics needs."""

    __slots__ = ("id", "description", "category", "cost", "currency", "date",
                 "month", "day", "shares")

    def __init__(self, raw: Dict[str, Any]):
        self.id = raw.get("id")
        self.description = raw.get("description") or ""
        cat = raw.get("category") or {}
        self.category = cat.get("name") or "Uncategorized"
        self.cost = _money(raw.get("cost"))
        self.currency = raw.get("currency_code") or "?"
        date = raw.get("date") or ""
        self.date = date
        self.month = date[:7] if len(date) >= 7 else "unknown"   # YYYY-MM
        self.day = date[:10] if len(date) >= 10 else date
        # shares: user_id -> (name, paid, owed, net)
        self.shares: Dict[int, Tuple[str, Decimal, Decimal, Decimal]] = {}
        for u in raw.get("users") or []:
            uid = u.get("user_id")
            if uid is None and isinstance(u.get("user"), dict):
                uid = u["user"].get("id")
            if uid is None:
                continue
            info = u.get("user") or {}
            name = (f"{info.get('first_name', '')} {info.get('last_name', '') or ''}".strip()
                    or f"User {uid}")
            paid = _money(u.get("paid_share"))
            owed = _money(u.get("owed_share"))
            net = _money(u.get("net_balance")) if u.get("net_balance") is not None else (paid - owed)
            self.shares[uid] = (name, paid, owed, net)

    def owed_of(self, user_id: int) -> Decimal:
        s = self.shares.get(user_id)
        return s[2] if s else ZERO

    def paid_of(self, user_id: int) -> Decimal:
        s = self.shares.get(user_id)
        return s[1] if s else ZERO


def normalize(raw_expenses: List[Dict[str, Any]]) -> List[NormalizedExpense]:
    """Drop payments (settle-ups) and deleted expenses; keep real spend only."""
    out = []
    for raw in raw_expenses:
        if raw.get("payment") is True:
            continue
        if raw.get("deleted_at"):
            continue
        out.append(NormalizedExpense(raw))
    return out


# ----------------------------------------------------------------------------
# Browser dataset (for interactive, client-side re-filtering)
# ----------------------------------------------------------------------------

def _paise(d: Decimal) -> int:
    """Exact integer paise (₹1 = 100). Client math stays integer -> no float drift."""
    return int(_q(d) * 100)


def build_dataset(
    raw_expenses: List[Dict[str, Any]],
    current_user_id: int,
    target_type: str,
    target_id: Optional[int] = None,
    target_label: str = "",
    truncated: bool = False,
    pages_fetched: int = 0,
) -> Dict[str, Any]:
    """Emit a compact, JSON-serializable dataset the browser can re-filter and
    recompute against — every amount as integer paise. The client JS engine mirrors
    compute_analytics exactly; `verify_total_paise` lets the page cross-check the JS
    full-range total against this Python-computed value (the drift guard).
    """
    expenses = normalize(raw_expenses)
    currencies = sorted({e.currency for e in expenses})
    cur_counts: Dict[str, int] = defaultdict(int)
    for e in expenses:
        cur_counts[e.currency] += 1
    primary = (sorted(cur_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
               if cur_counts else "")

    names: Dict[int, str] = {}
    rows = []
    for e in expenses:
        shares = {}
        for uid, (name, paid, owed, _net) in e.shares.items():
            names[uid] = name
            shares[str(uid)] = [_paise(paid), _paise(owed)]
        rows.append({
            "id": e.id,
            "date": e.day,
            "month": e.month,
            "desc": e.description,
            "cat": e.category,
            "cur": e.currency,
            "cost": _paise(e.cost),
            "shares": shares,
        })

    # Python-computed full-range total (group perspective = full cost) as drift guard.
    if target_type == "group":
        verify = sum(_paise(e.cost) for e in expenses)
    else:
        verify = sum(_paise(e.owed_of(current_user_id)) for e in expenses)

    return {
        "meta": {
            "target_type": target_type,
            "target_id": target_id,
            "target_label": target_label,
            "current_user_id": current_user_id,
            "primary_currency": primary,
            "currencies": currencies,
            "mixed_currency": len(currencies) > 1,
            "truncated": truncated,
            "pages_fetched": pages_fetched,
            "verify_total_paise": verify,
        },
        "names": {str(k): v for k, v in names.items()},
        "expenses": rows,
    }


# ----------------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------------

def compute_analytics(
    raw_expenses: List[Dict[str, Any]],
    current_user_id: int,
    target_type: str,
    target_id: Optional[int] = None,
    target_label: str = "",
    top_n: int = 10,
    truncated: bool = False,
    pages_fetched: int = 0,
) -> Dict[str, Any]:
    """Compute the full analytics result.

    target_type: "me" | "group" | "friend".
    Perspective is always the current (authenticated) user.
      - me/friend: per-expense "amount" = current user's owed_share (personal consumption).
      - group:     per-expense "amount" = full cost (group spend).
    Member comparison (#4) and settlement (#7) span all members and are group-only.
    """
    expenses = normalize(raw_expenses)

    result: Dict[str, Any] = {
        "meta": {
            "target_type": target_type,
            "target_id": target_id,
            "target_label": target_label,
            "current_user_id": current_user_id,
            "expense_count": len(expenses),
            "truncated": truncated,
            "pages_fetched": pages_fetched,
        }
    }

    if not expenses:
        result["empty"] = True
        return result
    result["empty"] = False

    # --- currency guard -----------------------------------------------------
    currencies = sorted({e.currency for e in expenses})
    mixed = len(currencies) > 1
    result["meta"]["currencies"] = currencies
    result["meta"]["mixed_currency"] = mixed
    # primary currency = the one with the most expenses (deterministic tie-break by code)
    cur_counts = defaultdict(int)
    for e in expenses:
        cur_counts[e.currency] += 1
    primary_currency = sorted(cur_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    result["meta"]["primary_currency"] = primary_currency

    is_group = target_type == "group"

    def amount_of(e: NormalizedExpense) -> Decimal:
        """Perspective amount for category/trend/ledger/top."""
        return e.cost if is_group else e.owed_of(current_user_id)

    # --- date span ----------------------------------------------------------
    days = sorted(e.day for e in expenses if e.day)
    result["meta"]["date_from"] = days[0] if days else None
    result["meta"]["date_to"] = days[-1] if days else None

    # =========================================================================
    # Module 1: Category breakdown
    # =========================================================================
    cat_totals: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    cat_counts: Dict[str, int] = defaultdict(int)
    for e in expenses:
        cat_totals[e.category] += amount_of(e)
        cat_counts[e.category] += 1
    grand = sum(cat_totals.values(), ZERO)
    categories = [
        {
            "category": c,
            "amount": _s(amt),
            "percentage": _pct(amt, grand),
            "count": cat_counts[c],
        }
        for c, amt in sorted(cat_totals.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    result["category_breakdown"] = {
        "total": _s(grand),
        "categories": categories,
    }

    # =========================================================================
    # Module 2: Monthly trend
    # =========================================================================
    month_totals: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    month_counts: Dict[str, int] = defaultdict(int)
    for e in expenses:
        month_totals[e.month] += amount_of(e)
        month_counts[e.month] += 1
    months = [
        {"month": m, "amount": _s(month_totals[m]), "count": month_counts[m]}
        for m in sorted(month_totals.keys())
    ]
    result["monthly_trend"] = {"months": months}

    # =========================================================================
    # Module 3: Owed-vs-paid-share  (always current user's perspective)
    # =========================================================================
    my_owed = sum((e.owed_of(current_user_id) for e in expenses), ZERO)
    my_paid = sum((e.paid_of(current_user_id) for e in expenses), ZERO)
    net = my_paid - my_owed
    result["owed_vs_paid"] = {
        "personal_share": _s(my_owed),      # what you consumed ("Mine + your split")
        "you_paid": _s(my_paid),            # cash you fronted
        "net": _s(net),                     # +ve => others owe you
        "fronted_for_others": _s(max(net, ZERO)),
        "you_owe_others": _s(max(-net, ZERO)),
    }

    # =========================================================================
    # Module 5: Transaction ledger  (rows backing every number)
    # =========================================================================
    ledger = []
    for e in sorted(expenses, key=lambda x: (x.date, x.id or 0)):
        ledger.append({
            "id": e.id,
            "date": e.day,
            "description": e.description,
            "category": e.category,
            "currency": e.currency,
            "cost": _s(e.cost),
            "your_share": _s(e.owed_of(current_user_id)),
            "you_paid": _s(e.paid_of(current_user_id)),
            "amount": _s(amount_of(e)),
        })
    result["ledger"] = ledger

    # =========================================================================
    # Module 6: Top transactions (by perspective amount)
    # =========================================================================
    top = sorted(expenses, key=lambda e: (-amount_of(e), e.date, e.id or 0))[:top_n]
    result["top_transactions"] = [
        {
            "id": e.id,
            "date": e.day,
            "description": e.description,
            "category": e.category,
            "amount": _s(amount_of(e)),
            "cost": _s(e.cost),
        }
        for e in top
    ]

    # =========================================================================
    # Modules 4 & 7: group-only (member comparison, category matrix, settlement)
    # =========================================================================
    if is_group:
        result.update(_group_modules(expenses, current_user_id, top_n))

    # =========================================================================
    # Reconciliation (determinism guardrail)
    # =========================================================================
    result["reconciliation"] = _reconcile(expenses, primary_currency, mixed)

    return result


def _group_modules(expenses, current_user_id, top_n) -> Dict[str, Any]:
    """Per-member comparison + category matrix + settlement optimizer."""
    names: Dict[int, str] = {}
    member_owed: Dict[int, Decimal] = defaultdict(lambda: ZERO)
    member_paid: Dict[int, Decimal] = defaultdict(lambda: ZERO)
    net: Dict[int, Decimal] = defaultdict(lambda: ZERO)
    matrix: Dict[Tuple[str, int], Decimal] = defaultdict(lambda: ZERO)
    all_categories = set()

    for e in expenses:
        all_categories.add(e.category)
        for uid, (name, paid, owed, n) in e.shares.items():
            names[uid] = name
            member_owed[uid] += owed
            member_paid[uid] += paid
            net[uid] += (paid - owed)
            matrix[(e.category, uid)] += owed

    grand_total = sum(member_owed.values(), ZERO)

    # ranked member summary
    ranked_ids = sorted(member_owed.keys(), key=lambda u: (-member_owed[u], names.get(u, "")))
    members = []
    for rank, uid in enumerate(ranked_ids, 1):
        # top category for this member
        cats = [(c, matrix[(c, uid)]) for c in all_categories if matrix[(c, uid)] > ZERO]
        top_cat = max(cats, key=lambda kv: (kv[1], kv[0])) if cats else ("N/A", ZERO)
        members.append({
            "rank": rank,
            "user_id": uid,
            "name": names[uid],
            "is_you": uid == current_user_id,
            "total_share": _s(member_owed[uid]),
            "total_paid": _s(member_paid[uid]),
            "net": _s(net[uid]),
            "percentage_of_total": _pct(member_owed[uid], grand_total),
            "top_category": top_cat[0],
            "top_category_amount": _s(top_cat[1]),
        })

    # category x member matrix
    ordered_cats = sorted(all_categories, key=lambda c: (-sum(matrix[(c, u)] for u in ranked_ids), c))
    matrix_rows = []
    for c in ordered_cats:
        row = {"category": c, "per_member": {}, "total": ZERO}
        for uid in ranked_ids:
            v = matrix[(c, uid)]
            row["per_member"][uid] = _s(v)
            row["total"] += v
        row["total"] = _s(row["total"])
        matrix_rows.append(row)

    # insights
    insights = {}
    if members:
        totals = [member_owed[u] for u in ranked_ids]
        avg = grand_total / Decimal(len(ranked_ids))
        insights = {
            "group_total": _s(grand_total),
            "member_count": len(ranked_ids),
            "average_per_member": _s(avg),
            "highest_spender": {"name": members[0]["name"], "amount": members[0]["total_share"]},
            "lowest_spender": {"name": members[-1]["name"], "amount": members[-1]["total_share"]},
            "spread": _s(totals[0] - totals[-1]) if totals else "0.00",
        }

    return {
        "member_comparison": {
            "members": members,
            "member_order": ranked_ids,
            "member_names": {uid: names[uid] for uid in ranked_ids},
            "insights": insights,
        },
        "category_matrix": {
            "member_order": ranked_ids,
            "rows": matrix_rows,
        },
        "settlement": _settle(net, names),
    }


def _settle(net: Dict[int, Decimal], names: Dict[int, str]) -> Dict[str, Any]:
    """Greedy min-cash-flow settlement. Deterministic; produces <= n-1 transactions.

    Match the largest creditor with the largest debtor, emit a payment, reduce both,
    advance whichever hit zero. Ties broken by (amount desc, user_id asc).
    """
    creditors = sorted(([uid, amt] for uid, amt in net.items() if amt > ZERO),
                       key=lambda x: (-x[1], x[0]))
    debtors = sorted(([uid, -amt] for uid, amt in net.items() if amt < ZERO),
                     key=lambda x: (-x[1], x[0]))
    txns = []
    i = j = 0
    while i < len(creditors) and j < len(debtors):
        c_uid, c_amt = creditors[i]
        d_uid, d_amt = debtors[j]
        pay = min(c_amt, d_amt)
        txns.append({
            "from_user_id": d_uid,
            "from_name": names.get(d_uid, f"User {d_uid}"),
            "to_user_id": c_uid,
            "to_name": names.get(c_uid, f"User {c_uid}"),
            "amount": _s(pay),
        })
        creditors[i][1] -= pay
        debtors[j][1] -= pay
        if creditors[i][1] == ZERO:
            i += 1
        if debtors[j][1] == ZERO:
            j += 1
    return {"transactions": txns, "transaction_count": len(txns)}


def _reconcile(expenses, primary_currency, mixed) -> Dict[str, Any]:
    """Verify sum(owed_share) == sum(cost) per currency (each expense's shares must
    sum to its cost). Flags any drift instead of returning authoritative-looking
    numbers that are wrong."""
    by_cur_cost: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_cur_owed: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    for e in expenses:
        by_cur_cost[e.currency] += e.cost
        by_cur_owed[e.currency] += sum((owed for (_n, _p, owed, _net) in e.shares.values()), ZERO)

    per_currency = []
    reconciled = True
    for cur in sorted(by_cur_cost.keys()):
        cost = by_cur_cost[cur]
        owed = by_cur_owed[cur]
        diff = (cost - owed).copy_abs()
        ok = diff <= TWO_PLACES
        reconciled = reconciled and ok
        per_currency.append({
            "currency": cur,
            "sum_cost": _s(cost),
            "sum_shares": _s(owed),
            "discrepancy": _s(diff),
            "ok": ok,
        })
    return {
        "reconciled": reconciled,
        "mixed_currency": mixed,
        "per_currency": per_currency,
    }
