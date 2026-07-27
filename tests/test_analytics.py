"""Deterministic-analytics unit tests.

Fixtures mirror the real Splitwise API expense shape. No network. The point of
these tests is to prove the money math is exact and reproducible.
"""

from decimal import Decimal

from splitwise_mcp_server.analytics import compute_analytics, normalize, _money

ME = 100
A = 200   # Ashutosh
B = 300   # abhi
C = 400   # Dinesh


def _user(uid, name, paid, owed):
    return {
        "user_id": uid,
        "user": {"id": uid, "first_name": name, "last_name": ""},
        "paid_share": str(paid),
        "owed_share": str(owed),
        "net_balance": str(Decimal(str(paid)) - Decimal(str(owed))),
    }


def _expense(eid, desc, cost, cat, date, users, currency="INR", payment=False, deleted=False):
    return {
        "id": eid,
        "description": desc,
        "cost": str(cost),
        "currency_code": currency,
        "category": {"id": 1, "name": cat},
        "date": date,
        "payment": payment,
        "deleted_at": "2020-01-01T00:00:00Z" if deleted else None,
        "users": users,
    }


# --- fixtures --------------------------------------------------------------

def equal_split_group():
    """₹400 equally 4 ways; ME paid the whole thing."""
    return [_expense(
        1, "Dinner", "400.00", "Food", "2026-01-05T10:00:00Z",
        [_user(ME, "Uday", "400.00", "100.00"),
         _user(A, "Ashutosh", "0", "100.00"),
         _user(B, "abhi", "0", "100.00"),
         _user(C, "Dinesh", "0", "100.00")],
    )]


def unequal_split_grocery():
    """The real ₹3,351 grocery case: ME owes 1789.50, others 520.50 each; ME paid all."""
    return [_expense(
        2, "Groceries", "3351.00", "General", "2026-01-25T10:00:00Z",
        [_user(ME, "Uday", "3351.00", "1789.50"),
         _user(A, "Ashutosh", "0", "520.50"),
         _user(B, "abhi", "0", "520.50"),
         _user(C, "Dinesh", "0", "520.50")],
    )]


# --- tests -----------------------------------------------------------------

def test_money_parses_without_float_noise():
    assert _money("0.1") + _money("0.2") == Decimal("0.3")
    assert _money(None) == Decimal("0")
    assert _money("") == Decimal("0")
    assert _money("garbage") == Decimal("0")


def test_payments_and_deleted_excluded():
    raw = equal_split_group() + [
        _expense(9, "Payment", "500", "Payment", "2026-01-06T00:00:00Z",
                 [_user(ME, "Uday", "500", "0"), _user(A, "Ashutosh", "0", "500")], payment=True),
        _expense(10, "Deleted thing", "999", "Food", "2026-01-07T00:00:00Z",
                 [_user(ME, "Uday", "999", "999")], deleted=True),
    ]
    assert len(normalize(raw)) == 1


def test_unequal_split_uses_owed_share_directly():
    """No 'Flight to' hack: personal share is read straight from owed_share."""
    r = compute_analytics(unequal_split_grocery(), ME, "me")
    assert r["owed_vs_paid"]["personal_share"] == "1789.50"
    assert r["owed_vs_paid"]["you_paid"] == "3351.00"
    # you fronted 3351 - 1789.50 = 1561.50 for others
    assert r["owed_vs_paid"]["net"] == "1561.50"
    assert r["owed_vs_paid"]["fronted_for_others"] == "1561.50"
    assert r["owed_vs_paid"]["you_owe_others"] == "0.00"


def test_me_perspective_category_uses_your_share():
    r = compute_analytics(unequal_split_grocery(), ME, "me")
    cats = r["category_breakdown"]
    assert cats["total"] == "1789.50"          # your share, not full 3351
    assert cats["categories"][0]["category"] == "General"
    assert cats["categories"][0]["amount"] == "1789.50"
    assert cats["categories"][0]["percentage"] == "100.0"


def test_group_perspective_category_uses_full_cost():
    r = compute_analytics(unequal_split_grocery(), ME, "group", target_id=1)
    assert r["category_breakdown"]["total"] == "3351.00"   # full group spend


def test_category_percentages_sum_to_100():
    raw = equal_split_group() + unequal_split_grocery()
    r = compute_analytics(raw, ME, "group", target_id=1)
    pcts = [Decimal(c["percentage"]) for c in r["category_breakdown"]["categories"]]
    assert abs(sum(pcts) - Decimal("100")) <= Decimal("0.1")


def test_monthly_buckets_across_year_boundary():
    raw = [
        _expense(1, "Dec", "100", "Food", "2025-12-20T00:00:00Z",
                 [_user(ME, "Uday", "100", "100")]),
        _expense(2, "Jan", "200", "Food", "2026-01-10T00:00:00Z",
                 [_user(ME, "Uday", "200", "200")]),
        _expense(3, "Jan2", "50", "Food", "2026-01-15T00:00:00Z",
                 [_user(ME, "Uday", "50", "50")]),
    ]
    r = compute_analytics(raw, ME, "me")
    months = {m["month"]: m for m in r["monthly_trend"]["months"]}
    assert months["2025-12"]["amount"] == "100.00"
    assert months["2026-01"]["amount"] == "250.00"
    assert months["2026-01"]["count"] == 2
    # sorted chronologically
    assert [m["month"] for m in r["monthly_trend"]["months"]] == ["2025-12", "2026-01"]


def test_multi_currency_flagged_and_not_summed_across():
    raw = [
        _expense(1, "INR thing", "100", "Food", "2026-01-01T00:00:00Z",
                 [_user(ME, "Uday", "100", "100")], currency="INR"),
        _expense(2, "USD thing", "50", "Food", "2026-01-02T00:00:00Z",
                 [_user(ME, "Uday", "50", "50")], currency="USD"),
    ]
    r = compute_analytics(raw, ME, "me")
    assert r["meta"]["mixed_currency"] is True
    assert r["meta"]["currencies"] == ["INR", "USD"]
    # reconciliation segments per currency, never cross-sums
    per = {p["currency"]: p for p in r["reconciliation"]["per_currency"]}
    assert per["INR"]["sum_cost"] == "100.00"
    assert per["USD"]["sum_cost"] == "50.00"
    assert r["reconciliation"]["reconciled"] is True


def test_reconciliation_flags_inconsistent_shares():
    """Shares that don't sum to cost must be flagged, not silently reported."""
    bad = [_expense(
        1, "Broken", "100.00", "Food", "2026-01-01T00:00:00Z",
        [_user(ME, "Uday", "100.00", "40.00"),
         _user(A, "Ashutosh", "0", "40.00")],  # 40+40 != 100
    )]
    r = compute_analytics(bad, ME, "group", target_id=1)
    assert r["reconciliation"]["reconciled"] is False
    assert Decimal(r["reconciliation"]["per_currency"][0]["discrepancy"]) == Decimal("20.00")


def test_member_comparison_ranked_and_you_flagged():
    raw = equal_split_group() + unequal_split_grocery()
    r = compute_analytics(raw, ME, "group", target_id=1)
    members = r["member_comparison"]["members"]
    # ME owes 100 + 1789.50 = 1889.50; each other owes 100 + 520.50 = 620.50
    me_row = next(m for m in members if m["user_id"] == ME)
    assert me_row["rank"] == 1
    assert me_row["is_you"] is True
    assert me_row["total_share"] == "1889.50"
    other = next(m for m in members if m["user_id"] == A)
    assert other["total_share"] == "620.50"


def test_settlement_nets_to_zero_and_minimal():
    """ME paid everything; 3 others each owe their share. Expect exactly 3 txns to ME."""
    raw = equal_split_group()   # ME net +300, each other -100
    r = compute_analytics(raw, ME, "group", target_id=1)
    st = r["settlement"]
    assert st["transaction_count"] == 3
    assert all(t["to_user_id"] == ME for t in st["transactions"])
    total_paid = sum(Decimal(t["amount"]) for t in st["transactions"])
    assert total_paid == Decimal("300.00")


def test_settlement_deterministic_ordering():
    raw = equal_split_group()
    r1 = compute_analytics(raw, ME, "group", target_id=1)["settlement"]
    r2 = compute_analytics(raw, ME, "group", target_id=1)["settlement"]
    assert r1 == r2   # byte-identical across runs
    # debtors ordered by (amount desc, user_id asc) -> A(200), B(300), C(400)
    assert [t["from_user_id"] for t in r1["transactions"]] == [A, B, C]


def test_empty_result_is_explicit():
    r = compute_analytics([], ME, "me")
    assert r["empty"] is True
    assert "category_breakdown" not in r


def test_ledger_sorted_by_date():
    raw = [
        _expense(2, "Second", "50", "Food", "2026-02-01T00:00:00Z",
                 [_user(ME, "Uday", "50", "50")]),
        _expense(1, "First", "50", "Food", "2026-01-01T00:00:00Z",
                 [_user(ME, "Uday", "50", "50")]),
    ]
    r = compute_analytics(raw, ME, "me")
    assert [row["description"] for row in r["ledger"]] == ["First", "Second"]


def test_full_run_is_byte_identical():
    """Determinism: two runs of the full pipeline produce equal output."""
    raw = equal_split_group() + unequal_split_grocery()
    assert compute_analytics(raw, ME, "group", target_id=1) == \
        compute_analytics(raw, ME, "group", target_id=1)
