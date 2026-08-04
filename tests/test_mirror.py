"""Local SQLite mirror tests — pure, temp db, no network."""

import sqlite3
import pytest

from splitwise_mcp_server import mirror

U, D = 27999405, 50113999


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "test.db"))
    c.row_factory = sqlite3.Row
    mirror.init_schema(c)
    yield c
    c.close()


def expense(eid, desc, cost, cat="Gas/fuel", date="2026-07-01T12:00:00Z",
            group_id=101345410, deleted=False, payment=False, details=""):
    return {
        "id": eid, "group_id": group_id, "description": desc, "details": details,
        "cost": str(cost), "currency_code": "INR", "category": {"id": 33, "name": cat},
        "date": date, "created_at": date, "updated_at": date,
        "deleted_at": "2026-07-02T00:00:00Z" if deleted else None,
        "payment": payment,
        "users": [
            {"user_id": U, "user": {"first_name": "Uday", "last_name": "G"},
             "paid_share": str(cost), "owed_share": str(cost/2)},
            {"user_id": D, "user": {"first_name": "Dinesh", "last_name": "S"},
             "paid_share": "0.0", "owed_share": str(cost/2)},
        ],
    }


def test_insert_then_reupsert_no_dup(conn):
    assert mirror.upsert_expense(conn, expense(1, "Petrol", 2000)) == "new"
    # re-upsert same id with a changed description -> updated, still 1 row
    assert mirror.upsert_expense(conn, expense(1, "Petrol (renamed)", 2000)) == "updated"
    rows = conn.execute("SELECT * FROM expenses").fetchall()
    assert len(rows) == 1
    assert rows[0]["description"] == "Petrol (renamed)"


def test_deleted_flagged_and_excluded(conn):
    mirror.upsert_expense(conn, expense(2, "Old fuel", 500))
    assert mirror.upsert_expense(conn, expense(2, "Old fuel", 500, deleted=True)) == "deleted"
    # default search excludes deleted
    assert mirror.search(conn, group_id=101345410) == []
    # include_deleted surfaces it
    assert len(mirror.search(conn, group_id=101345410, include_deleted=True)) == 1


def test_search_by_text_fts(conn):
    mirror.upsert_expense(conn, expense(3, "Bike purchase", 90000, cat="Bicycle"))
    mirror.upsert_expense(conn, expense(4, "Petrol top-up", 2000))
    hits = mirror.search(conn, query="bike")
    assert len(hits) == 1 and hits[0]["id"] == 3


def test_search_by_amount_range(conn):
    mirror.upsert_expense(conn, expense(5, "small", 500))
    mirror.upsert_expense(conn, expense(6, "big", 90000))
    hits = mirror.search(conn, min_amount=1000)
    assert [h["id"] for h in hits] == [6]


def test_search_by_user(conn):
    mirror.upsert_expense(conn, expense(7, "shared", 1000))
    # both U and D are on it
    assert len(mirror.search(conn, user_id=D)) == 1
    assert len(mirror.search(conn, user_id=999)) == 0


def test_search_by_category_and_date(conn):
    mirror.upsert_expense(conn, expense(8, "fuel", 2000, cat="Gas/fuel", date="2026-06-01T00:00:00Z"))
    mirror.upsert_expense(conn, expense(9, "food", 800, cat="Dining out", date="2026-07-15T00:00:00Z"))
    assert [h["id"] for h in mirror.search(conn, category="Dining out")] == [9]
    assert [h["id"] for h in mirror.search(conn, dated_after="2026-07-01T00:00:00Z")] == [9]


def test_payments_filter(conn):
    mirror.upsert_expense(conn, expense(10, "Payment", 3413.06, payment=True))
    mirror.upsert_expense(conn, expense(11, "Petrol", 2000))
    assert len(mirror.search(conn)) == 2
    assert [h["id"] for h in mirror.search(conn, include_payments=False)] == [11]


def test_cursor_roundtrip(conn):
    assert mirror.get_cursor(conn) is None
    mirror.set_cursor(conn, "2026-08-04T00:00:00Z")
    assert mirror.get_cursor(conn) == "2026-08-04T00:00:00Z"
    mirror.set_cursor(conn, "2026-08-05T00:00:00Z")   # overwrite
    assert mirror.get_cursor(conn) == "2026-08-05T00:00:00Z"


def test_expense_users_populated(conn):
    mirror.upsert_expense(conn, expense(12, "shared", 1000))
    rows = conn.execute("SELECT * FROM expense_users WHERE expense_id=12 ORDER BY user_id").fetchall()
    assert len(rows) == 2
    uday = [r for r in rows if r["user_id"] == U][0]
    assert uday["paid_share"] == 1000.0 and uday["owed_share"] == 500.0


def test_stats(conn):
    mirror.upsert_expense(conn, expense(13, "a", 100))
    mirror.upsert_expense(conn, expense(14, "b", 200, deleted=True))
    mirror.upsert_group(conn, {"id": 1, "name": "Roomies", "group_type": "home", "updated_at": "x"})
    mirror.upsert_friend(conn, {"id": D, "first_name": "Dinesh", "last_name": "S"})
    s = mirror.stats(conn)
    assert s["expenses"] == 1 and s["deleted"] == 1 and s["groups"] == 1 and s["friends"] == 1


def test_raw_is_faithful(conn):
    """Search returns the exact raw object, not the lossy REAL columns."""
    e = expense(15, "Exact", 3351)
    e["cost"] = "3351.00"  # string preserved
    mirror.upsert_expense(conn, e)
    got = mirror.search(conn, query="exact")[0]
    assert got["cost"] == "3351.00"   # faithful string, not 3351.0 float
