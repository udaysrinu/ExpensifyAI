"""Itemization math tests — exact integer-paise splits, per-item shares."""

import pytest

from splitwise_mcp_server.itemize import (
    aggregate_items, _to_paise, _rupees, _largest_remainder, ItemizeError,
)

UDAY, ASHU, ABHI, DINESH = 1, 2, 3, 4


def owed_of(res, uid):
    return res["per_user"][uid]["owed"]


def paid_of(res, uid):
    return res["per_user"][uid]["paid"]


# --- primitives ------------------------------------------------------------

def test_paise_roundtrip():
    assert _to_paise("3351.00") == 335100
    assert _to_paise(520.5) == 52050
    assert _rupees(52050) == "520.50"
    assert _rupees(335100) == "3351.00"


def test_largest_remainder_sums_exactly():
    # ₹100 three ways -> 33.34 / 33.33 / 33.33, sums to 100.00
    parts = _largest_remainder(10000, [1, 1, 1])
    assert sum(parts) == 10000
    assert sorted(parts) == [3333, 3333, 3334]


def test_largest_remainder_deterministic():
    assert _largest_remainder(10000, [1, 1, 1]) == _largest_remainder(10000, [1, 1, 1])


# --- equal split -----------------------------------------------------------

def test_equal_split_indivisible():
    res = aggregate_items([
        {"desc": "Dinner", "amount": "100.00", "paid_by": UDAY,
         "split": {"type": "equal", "among": [UDAY, ASHU, ABHI]}},
    ])
    assert res["reconciled"]
    assert paid_of(res, UDAY) == 10000
    # owed sums to exactly 10000
    assert sum(res["per_user"][u]["owed"] for u in res["per_user"]) == 10000


# --- shares split (beers 3/4 Ashu, 1/4 Uday) -------------------------------

def test_shares_split_beers():
    res = aggregate_items([
        {"desc": "Beers", "amount": "2710.00", "paid_by": UDAY,
         "split": {"type": "shares", "among": [UDAY, ASHU], "shares": {UDAY: 1, ASHU: 3}}},
    ])
    assert res["reconciled"]
    assert owed_of(res, UDAY) == 67750    # ₹677.50
    assert owed_of(res, ASHU) == 203250   # ₹2032.50


# --- exact split validation ------------------------------------------------

def test_exact_split_rejects_mismatch():
    with pytest.raises(ItemizeError):
        aggregate_items([
            {"desc": "Thing", "amount": "100.00", "paid_by": UDAY,
             "split": {"type": "exact", "exact": {UDAY: "40.00", ASHU: "40.00"}}},  # 80 != 100
        ])


def test_exact_split_ok():
    res = aggregate_items([
        {"desc": "Thing", "amount": "100.00", "paid_by": UDAY,
         "split": {"type": "exact", "exact": {UDAY: "60.00", ASHU: "40.00"}}},
    ])
    assert res["reconciled"]
    assert owed_of(res, UDAY) == 6000 and owed_of(res, ASHU) == 4000


# --- the real batch: New Year (beers shares + party/cake you+ashu equal) ---

def test_real_new_year_batch():
    res = aggregate_items([
        {"desc": "Beers", "amount": "2710.00", "paid_by": UDAY,
         "split": {"type": "shares", "among": [UDAY, ASHU], "shares": {UDAY: 1, ASHU: 3}}},
        {"desc": "Party stuff", "amount": "1498.00", "paid_by": UDAY,
         "split": {"type": "equal", "among": [UDAY, ASHU]}},
        {"desc": "Cake", "amount": "854.00", "paid_by": UDAY,
         "split": {"type": "equal", "among": [UDAY, ASHU]}},
    ], cost="5062.00")
    assert res["reconciled"]
    assert res["total_paise"] == 506200
    # Uday: 677.50 + 749 + 427 = 1853.50 ; Ashu: 2032.50 + 749 + 427 = 3208.50
    assert owed_of(res, UDAY) == 185350
    assert owed_of(res, ASHU) == 320850
    assert paid_of(res, UDAY) == 506200


def test_cost_mismatch_flagged():
    res = aggregate_items([
        {"desc": "A", "amount": "100.00", "paid_by": UDAY,
         "split": {"type": "equal", "among": [UDAY, ASHU]}},
    ], cost="200.00")   # declared cost != sum of items
    assert res["reconciled"] is False


# --- split_ref expansion from saved templates ------------------------------

def test_split_ref_expansion():
    templates = {"roomies-4way": {"type": "equal", "among": [UDAY, ASHU, ABHI, DINESH]}}
    res = aggregate_items([
        {"desc": "Groceries", "amount": "4000.00", "paid_by": UDAY, "split_ref": "roomies-4way"},
    ], default_splits=templates)
    assert res["reconciled"]
    for u in (UDAY, ASHU, ABHI, DINESH):
        assert owed_of(res, u) == 100000   # ₹1000 each


def test_unknown_split_ref_errors():
    with pytest.raises(ItemizeError):
        aggregate_items([{"desc": "x", "amount": "10.00", "paid_by": UDAY, "split_ref": "nope"}])


# --- guards ----------------------------------------------------------------

def test_empty_items_errors():
    with pytest.raises(ItemizeError):
        aggregate_items([])


def test_nonpositive_amount_errors():
    with pytest.raises(ItemizeError):
        aggregate_items([{"desc": "x", "amount": "0", "paid_by": UDAY,
                          "split": {"type": "equal", "among": [UDAY]}}])


def test_users_array_shape():
    res = aggregate_items([
        {"desc": "G", "amount": "1000.00", "paid_by": UDAY,
         "split": {"type": "equal", "among": [UDAY, ASHU]}},
    ])
    users = {u["user_id"]: u for u in res["users"]}
    assert users[UDAY]["paid_share"] == "1000.00"
    assert users[UDAY]["owed_share"] == "500.00"
    assert users[ASHU]["paid_share"] == "0.00"
    assert users[ASHU]["owed_share"] == "500.00"
