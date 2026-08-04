"""Local SQLite mirror of Splitwise data — enables offline delta sync + fast search.

Pure DB layer: no network. The sync tool (server.py) fetches from the API and calls
`upsert_*` here; searches/queries run entirely against the local DB. Each expense's full
raw API object is stored in `raw` (source of truth) so displayed values are faithful and
never suffer float drift — the REAL columns exist only for querying/filtering.

Delta sync uses `get_cursor()`/`set_cursor()`: the API's `updated_after` returns every
expense whose record changed (new, edited, moved, or deleted) since the cursor, so upserts
by `id` keep the mirror correct without a full reload.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def default_path() -> str:
    return os.getenv("EXPENSIFYAI_DB_PATH") or str(Path.home() / ".expensifyai" / "splitwise.db")


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    p = Path(path or default_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY, name TEXT, group_type TEXT, updated_at TEXT, raw TEXT);
    CREATE TABLE IF NOT EXISTS friends(
        id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, raw TEXT);
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY, group_id INTEGER, description TEXT, details TEXT,
        cost REAL, currency TEXT, category TEXT, date TEXT, created_at TEXT,
        updated_at TEXT, deleted INTEGER DEFAULT 0, payment INTEGER DEFAULT 0, raw TEXT);
    CREATE TABLE IF NOT EXISTS expense_users(
        expense_id INTEGER, user_id INTEGER, name TEXT,
        paid_share REAL, owed_share REAL,
        PRIMARY KEY(expense_id, user_id));
    CREATE INDEX IF NOT EXISTS idx_exp_group ON expenses(group_id);
    CREATE INDEX IF NOT EXISTS idx_exp_date ON expenses(date);
    CREATE INDEX IF NOT EXISTS idx_eu_user ON expense_users(user_id);
    CREATE VIRTUAL TABLE IF NOT EXISTS expenses_fts USING fts5(
        description, details, category);
    """)
    conn.commit()


# --- helpers ---------------------------------------------------------------

def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _member_name(u: Dict[str, Any]) -> str:
    info = u.get("user") or {}
    return f"{info.get('first_name', '')} {info.get('last_name', '') or ''}".strip()


# --- cursor ----------------------------------------------------------------

def get_cursor(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_state WHERE key='last_synced_at'").fetchone()
    return row["value"] if row else None


def set_cursor(conn: sqlite3.Connection, ts: str) -> None:
    conn.execute("INSERT INTO sync_state(key,value) VALUES('last_synced_at',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (ts,))
    conn.commit()


# --- upserts ---------------------------------------------------------------

def upsert_expense(conn: sqlite3.Connection, raw: Dict[str, Any]) -> str:
    """Insert or replace an expense by id. Returns 'new' | 'updated' | 'deleted'."""
    eid = raw.get("id")
    if eid is None:
        return "skipped"
    existed = conn.execute("SELECT 1 FROM expenses WHERE id=?", (eid,)).fetchone() is not None
    deleted = 1 if raw.get("deleted_at") else 0
    cat = (raw.get("category") or {}).get("name")
    conn.execute("""
        INSERT INTO expenses(id,group_id,description,details,cost,currency,category,date,
            created_at,updated_at,deleted,payment,raw)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            group_id=excluded.group_id, description=excluded.description, details=excluded.details,
            cost=excluded.cost, currency=excluded.currency, category=excluded.category,
            date=excluded.date, updated_at=excluded.updated_at, deleted=excluded.deleted,
            payment=excluded.payment, raw=excluded.raw
    """, (eid, raw.get("group_id"), raw.get("description"), raw.get("details"),
          _f(raw.get("cost")), raw.get("currency_code"), cat, raw.get("date"),
          raw.get("created_at"), raw.get("updated_at"), deleted,
          1 if raw.get("payment") else 0, json.dumps(raw)))

    # refresh per-user shares
    conn.execute("DELETE FROM expense_users WHERE expense_id=?", (eid,))
    for u in raw.get("users") or []:
        uid = u.get("user_id") or (u.get("user") or {}).get("id")
        if uid is None:
            continue
        conn.execute("INSERT OR REPLACE INTO expense_users(expense_id,user_id,name,paid_share,owed_share)"
                     " VALUES(?,?,?,?,?)",
                     (eid, uid, _member_name(u), _f(u.get("paid_share")), _f(u.get("owed_share"))))

    # FTS: keep row in sync (delete + re-insert with rowid = expense id)
    conn.execute("DELETE FROM expenses_fts WHERE rowid=?", (eid,))
    if not deleted:
        conn.execute("INSERT INTO expenses_fts(rowid,description,details,category) VALUES(?,?,?,?)",
                     (eid, raw.get("description") or "", raw.get("details") or "", cat or ""))
    conn.commit()
    return "deleted" if deleted else ("updated" if existed else "new")


def upsert_group(conn: sqlite3.Connection, raw: Dict[str, Any]) -> None:
    conn.execute("INSERT INTO groups(id,name,group_type,updated_at,raw) VALUES(?,?,?,?,?)"
                 " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
                 " group_type=excluded.group_type, updated_at=excluded.updated_at, raw=excluded.raw",
                 (raw.get("id"), raw.get("name"), raw.get("group_type"),
                  raw.get("updated_at"), json.dumps(raw)))
    conn.commit()


def upsert_friend(conn: sqlite3.Connection, raw: Dict[str, Any]) -> None:
    conn.execute("INSERT INTO friends(id,first_name,last_name,raw) VALUES(?,?,?,?)"
                 " ON CONFLICT(id) DO UPDATE SET first_name=excluded.first_name,"
                 " last_name=excluded.last_name, raw=excluded.raw",
                 (raw.get("id"), raw.get("first_name"), raw.get("last_name"), json.dumps(raw)))
    conn.commit()


# --- search / query --------------------------------------------------------

def search(conn: sqlite3.Connection, query: Optional[str] = None,
           min_amount: Optional[float] = None, max_amount: Optional[float] = None,
           user_id: Optional[int] = None, group_id: Optional[int] = None,
           category: Optional[str] = None, dated_after: Optional[str] = None,
           dated_before: Optional[str] = None, include_deleted: bool = False,
           include_payments: bool = True, limit: int = 50) -> List[Dict[str, Any]]:
    """Search the mirror. Returns faithful raw expense objects (parsed from `raw`)."""
    where, params, joins = [], [], ""
    if query:
        joins = " JOIN expenses_fts f ON f.rowid = e.id "
        where.append("expenses_fts MATCH ?")
        params.append(query)
    if not include_deleted:
        where.append("e.deleted = 0")
    if not include_payments:
        where.append("e.payment = 0")
    if min_amount is not None:
        where.append("e.cost >= ?"); params.append(min_amount)
    if max_amount is not None:
        where.append("e.cost <= ?"); params.append(max_amount)
    if group_id is not None:
        where.append("e.group_id = ?"); params.append(group_id)
    if category:
        where.append("e.category = ?"); params.append(category)
    if dated_after:
        where.append("e.date >= ?"); params.append(dated_after)
    if dated_before:
        where.append("e.date <= ?"); params.append(dated_before)
    if user_id is not None:
        where.append("e.id IN (SELECT expense_id FROM expense_users WHERE user_id = ?)")
        params.append(user_id)

    sql = f"SELECT e.raw FROM expenses e{joins}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY e.date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [json.loads(r["raw"]) for r in rows]


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    return {
        "expenses": conn.execute("SELECT COUNT(*) c FROM expenses WHERE deleted=0").fetchone()["c"],
        "deleted": conn.execute("SELECT COUNT(*) c FROM expenses WHERE deleted=1").fetchone()["c"],
        "groups": conn.execute("SELECT COUNT(*) c FROM groups").fetchone()["c"],
        "friends": conn.execute("SELECT COUNT(*) c FROM friends").fetchone()["c"],
        "last_synced_at": get_cursor(conn),
    }


def fuzzy_search(conn: sqlite3.Connection, query: str, min_score: int = 70,
                 include_deleted: bool = False, limit: int = 25) -> List[Dict[str, Any]]:
    """Fuzzy full-text over description + details (notes), for when exact FTS misses.

    Catches typos, abbreviations (vizag≈vskp≈vtz), and — crucially — amounts/words buried
    inside a bundle's note (e.g. a '5552' line inside a multi-item details field). Uses
    rapidfuzz partial-ratio (already a repo dependency). An exact case-insensitive substring
    match always scores 100 so number-in-note lookups are reliable.

    Returns matches sorted by score desc, each = {score, matched_in, expense: <raw>}.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        # graceful fallback: substring only
        fuzz = None

    q = (query or "").strip().lower()
    if not q:
        return []
    sql = "SELECT raw, description, details FROM expenses"
    if not include_deleted:
        sql += " WHERE deleted = 0"
    scored = []
    for row in conn.execute(sql):
        desc = (row["description"] or "")
        det = (row["details"] or "")
        hay_desc, hay_det = desc.lower(), det.lower()
        # exact substring anywhere -> top score, note where it hit
        if q in hay_desc:
            score, where = 100, "description"
        elif q in hay_det:
            score, where = 100, "note"
        elif fuzz is not None:
            sd = fuzz.partial_ratio(q, hay_desc)
            st = fuzz.partial_ratio(q, hay_det) if hay_det else 0
            score = max(sd, st)
            where = "description" if sd >= st else "note"
        else:
            continue
        if score >= min_score:
            scored.append((score, where, json.loads(row["raw"])))
    scored.sort(key=lambda x: (-x[0], x[2].get("date", "")))
    return [{"score": s, "matched_in": w, "expense": e} for s, w, e in scored[:limit]]
