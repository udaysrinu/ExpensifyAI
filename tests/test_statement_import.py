"""Statement-import proposal tests — pure, deterministic."""

import pytest
from splitwise_mcp_server.statement_import import (
    build_proposal, suggest_category, _paise, _rupees,
)

ME = 100


def test_paise_parsing_handles_symbols_and_commas():
    assert _paise("1,312.00") == 131200
    assert _paise("₹497") == 49700
    assert _paise("-2000.50") == 200050   # sign stripped (magnitude)
    assert _rupees(131200) == "1312.00"


def test_category_suggestion():
    assert suggest_category("SWIGGY BANGALORE") == "Dining out"
    assert suggest_category("HPCL PETROL PUMP") == "Gas/fuel"
    assert suggest_category("BookMyShow Peddi") == "Entertainment"
    assert suggest_category("Zepto Marketplace") == "Groceries"
    assert suggest_category("Random merchant xyz") == "General"


def test_basic_proposal_personal():
    txns = [
        {"date": "2026-06-12", "merchant": "BookMyShow", "amount": "1312.00"},
        {"date": "2026-06-11", "merchant": "KFC", "amount": "497"},
    ]
    r = build_proposal(txns, ME)
    assert r["summary"]["count"] == 2
    assert r["summary"]["total"] == "1809.00"
    p0 = r["proposals"][0]
    assert p0["category"] == "Entertainment" and p0["split"] == "100% you (personal)"
    assert p0["include"] is True


def test_default_split_template_applied():
    txns = [{"date": "2026-06-12", "merchant": "Fuel", "amount": "2000"}]
    templates = {"me-dinesh": {"type": "equal", "among": [ME, 200]}}
    r = build_proposal(txns, ME, default_split_name="me-dinesh", default_splits=templates)
    assert r["proposals"][0]["split_ref"] == "me-dinesh"
    assert "me-dinesh" in r["proposals"][0]["split"]


def test_unknown_split_ref_falls_back_to_personal():
    txns = [{"date": "2026-06-12", "merchant": "Fuel", "amount": "2000", "split_ref": "nope"}]
    r = build_proposal(txns, ME, default_splits={})
    assert r["proposals"][0]["split_ref"] is None
    assert r["proposals"][0]["split"] == "100% you (personal)"


def test_duplicate_detection_flags_and_excludes():
    txns = [
        {"date": "2026-06-12", "merchant": "BookMyShow", "amount": "1312.00"},
        {"date": "2026-06-11", "merchant": "KFC", "amount": "497"},
    ]
    existing = [{"date": "2026-06-12T13:00:00Z", "cost": "1312.00"}]  # BMS already entered
    r = build_proposal(txns, ME, existing=existing)
    bms = next(p for p in r["proposals"] if p["description"] == "BookMyShow")
    kfc = next(p for p in r["proposals"] if p["description"] == "KFC")
    assert bms["duplicate_of_existing"] is True and bms["include"] is False
    assert kfc["duplicate_of_existing"] is False and kfc["include"] is True
    assert r["summary"]["likely_duplicates"] == 1
    assert r["summary"]["will_import"] == 1


def test_bad_amount_row_reported_not_crash():
    txns = [{"date": "2026-06-12", "merchant": "X", "amount": "N/A"}]
    r = build_proposal(txns, ME)
    assert "error" in r["proposals"][0]


def test_per_row_category_override():
    txns = [{"date": "2026-06-12", "merchant": "Random", "amount": "100", "category": "Gifts"}]
    r = build_proposal(txns, ME)
    assert r["proposals"][0]["category"] == "Gifts"   # explicit wins over suggestion
