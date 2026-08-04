"""Fuzzy note-search tests — pure, temp db."""

import sqlite3
import pytest
from splitwise_mcp_server import mirror


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "t.db"))
    c.row_factory = sqlite3.Row
    mirror.init_schema(c)
    yield c
    c.close()


def exp(eid, desc, details="", cost="100.00"):
    return {"id": eid, "group_id": None, "description": desc, "details": details,
            "cost": cost, "currency_code": "INR", "category": {"name": "General"},
            "date": "2026-01-18T12:00:00Z", "payment": False, "deleted_at": None,
            "users": [{"user_id": 1, "user": {"first_name": "U"}, "paid_share": cost, "owed_share": cost}]}


def test_number_buried_in_note_found(conn):
    mirror.upsert_expense(conn, exp(1, "Travel bundle",
        details="movie 1500\nbus return vizag-hyd 5552\nbiryani 800"))
    r = mirror.fuzzy_search(conn, "5552")
    assert len(r) == 1 and r[0]["matched_in"] == "note" and r[0]["score"] == 100


def test_abbreviation_typo_matches(conn):
    mirror.upsert_expense(conn, exp(2, "Uday train vskp to sc"))
    # 'vizag' vs 'vskp' — fuzzy should still surface it
    r = mirror.fuzzy_search(conn, "vskp", min_score=60)
    assert any(e["expense"]["id"] == 2 for e in r)


def test_substring_in_description_scores_100(conn):
    mirror.upsert_expense(conn, exp(3, "Bus tickets to home"))
    r = mirror.fuzzy_search(conn, "bus tickets")
    assert r[0]["score"] == 100 and r[0]["matched_in"] == "description"


def test_below_threshold_excluded(conn):
    mirror.upsert_expense(conn, exp(4, "Completely unrelated groceries"))
    r = mirror.fuzzy_search(conn, "xyzzy fuel petrol", min_score=80)
    assert r == []


def test_deleted_excluded_by_default(conn):
    e = exp(5, "Old travel note 5552", cost="5552.00")
    e["deleted_at"] = "2026-02-01T00:00:00Z"
    mirror.upsert_expense(conn, e)
    assert mirror.fuzzy_search(conn, "5552") == []
    assert len(mirror.fuzzy_search(conn, "5552", include_deleted=True)) == 1


def test_empty_query(conn):
    mirror.upsert_expense(conn, exp(6, "whatever"))
    assert mirror.fuzzy_search(conn, "") == []
