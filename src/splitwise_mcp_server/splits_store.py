"""Local JSON store for reusable split templates ("save default splits").

A template is a named split object, e.g.:
    "roomies-4way": {"type": "equal", "among": [id1, id2, id3, id4]}
    "me-ashu-5050": {"type": "equal", "among": [me, ashu]}

Stored at ~/.expensifyai/splits.json (override with EXPENSIFYAI_SPLITS_PATH).
Pure file I/O; no network. Kept tiny and dependency-free.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


def _store_path() -> Path:
    override = os.getenv("EXPENSIFYAI_SPLITS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".expensifyai" / "splits.json"


def load_all() -> Dict[str, Any]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save(name: str, split: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert a template; returns the full store after writing."""
    if not name:
        raise ValueError("template name is required")
    data = load_all()
    data[name] = split
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def get(name: str) -> Dict[str, Any] | None:
    return load_all().get(name)


def delete(name: str) -> bool:
    data = load_all()
    if name in data:
        del data[name]
        _store_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    return False
