"""Create + seed the local Phase 7A demo database.

Usage (from the backend directory):

    python -m app.demo.bootstrap

By default writes backend/demo.db (SQLite). Set DATABASE_URL to override:

    $env:DATABASE_URL = "sqlite+pysqlite:///./demo.db"
    python -m app.demo.bootstrap
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

DEFAULT_URL = "sqlite+pysqlite:///./demo.db"


def main() -> None:
    url = os.environ.get("DATABASE_URL", DEFAULT_URL)
    from app.demo.demo_seed import prepare_demo_database

    prepare_demo_database(url)
    print(f"[demo] demo database ready: {url}")
    print("[demo] orders: ORD-1001 (DELIVERED, refundable), ORD-1002 (PAID, cancellable), ORD-1003 (DELIVERED), ORD-2001 (Bob)")
    print("[demo] users: Alice Zhang (id=1), Bob Li (id=2), Carol Wang (id=3)")


if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND_DIR))
    main()
