"""migration, idempotence, preservation, future-version rejection.

All fixtures live in pytest tmp dirs — no binary databases committed.
Legacy databases are built by executing reduced DDL through raw sqlite3
(simulating the 7135edd five-table shape and the unstamped six-table
risk_state-era shape), never by copying real files.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.database import Database
from core.db_migrations import (
    CURRENT_SCHEMA_VERSION,
    DatabaseSchemaError,
    read_schema_version,
)

LEGACY_V1_SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    starting_balance REAL,
    ending_balance REAL,
    total_pnl REAL,
    num_trades INTEGER
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    order_type TEXT,
    oid INTEGER,
    cloid TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL NOT NULL,
    fee REAL DEFAULT 0.0,
    oid INTEGER,
    cloid TEXT,
    filled_at TEXT NOT NULL
);
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    size REAL NOT NULL,
    pnl REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    entry_time TEXT,
    exit_time TEXT NOT NULL
);
CREATE TABLE analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_type TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
"""

RISK_STATE_TABLE = """
CREATE TABLE risk_state (
    symbol TEXT NOT NULL,
    mode TEXT NOT NULL,
    day TEXT NOT NULL,
    session_id TEXT NOT NULL,
    realized_loss REAL NOT NULL DEFAULT 0.0,
    open_qty REAL NOT NULL DEFAULT 0.0,
    entry_price REAL NOT NULL DEFAULT 0.0,
    entry_fee REAL NOT NULL DEFAULT 0.0,
    basis_known INTEGER NOT NULL DEFAULT 0,
    total_pnl REAL NOT NULL DEFAULT 0.0,
    num_trades INTEGER NOT NULL DEFAULT 0,
    kill_engaged INTEGER NOT NULL DEFAULT 0,
    kill_reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, mode, day)
);
"""


def make_legacy_v1(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript(LEGACY_V1_SCHEMA)
    con.execute(
        "INSERT INTO sessions (session_id, symbol, strategy, mode, "
        "started_at, starting_balance) VALUES ('s-legacy', 'BTC', 'EMA', "
        "'paper', '2026-01-01T00:00:00+00:00', 10000.0)"
    )
    con.execute(
        "INSERT INTO trades (session_id, symbol, side, entry_price, "
        "exit_price, size, pnl, pnl_pct, exit_time) VALUES ('s-legacy', "
        "'BTC', 'LONG', 50000.0, 51000.0, 0.001, 1.0, 0.02, "
        "'2026-01-02T00:00:00+00:00')"
    )
    con.commit()
    con.close()


def make_unstamped_v2(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript(LEGACY_V1_SCHEMA + RISK_STATE_TABLE)
    con.execute(
        "INSERT INTO risk_state (symbol, mode, day, session_id, "
        "realized_loss, open_qty, entry_price, entry_fee, basis_known, "
        "total_pnl, num_trades, kill_engaged, kill_reason, updated_at) "
        "VALUES ('BTC', 'paper_trade', '2026-10-23', 's-kill', 123.45, "
        "0.5, 60000.0, 0.06, 1, -50.0, 7, 1, 'UNIT_TEST_KILL', "
        "'2026-10-23T00:00:00+00:00')"
    )
    con.commit()
    con.close()


# ── Legacy detection + additive migration ──


def test_legacy_v1_migrates_additively_preserving_rows(tmp_path) -> None:
    path = str(tmp_path / "legacy_v1.db")
    make_legacy_v1(path)
    db = Database(db_path=path)
    try:
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
        session = db.get_session("s-legacy")
        assert session is not None and session["symbol"] == "BTC"
        trades = db.list_trades(session_id="s-legacy")
        assert len(trades) == 1 and trades[0]["pnl"] == 1.0
    finally:
        db.close()


def test_unstamped_v2_preserves_kill_state(tmp_path) -> None:
    """The core P6 preservation proof: a latched kill survives migration."""
    path = str(tmp_path / "unstamped_v2.db")
    make_unstamped_v2(path)
    db = Database(db_path=path)
    try:
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
        row = db.load_risk_state("BTC", "paper_trade", "2026-10-23")
        assert row is not None
        assert row["kill_engaged"] == 1
        assert row["kill_reason"] == "UNIT_TEST_KILL"
        assert row["realized_loss"] == 123.45
        assert row["open_qty"] == 0.5
        assert row["entry_price"] == 60000.0
        assert row["entry_fee"] == 0.06
        assert row["total_pnl"] == -50.0
        assert row["num_trades"] == 7
        assert row["session_id"] == "s-kill"
    finally:
        db.close()


def test_migration_is_idempotent(tmp_path) -> None:
    path = str(tmp_path / "idempotent.db")
    make_legacy_v1(path)
    db1 = Database(db_path=path)
    db1.close()
    db2 = Database(db_path=path)  # second open = no-op, must not raise
    try:
        assert db2.get_schema_version() == CURRENT_SCHEMA_VERSION
        assert db2.get_session("s-legacy") is not None
    finally:
        db2.close()


def test_fresh_db_stamps_current_version(tmp_path) -> None:
    db = Database(db_path=str(tmp_path / "fresh.db"))
    try:
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
    finally:
        db.close()


# ── Pragma verification ──


def test_pragmas_verified_on_open(tmp_path) -> None:
    db = Database(db_path=str(tmp_path / "pragma.db"))
    try:
        journal = str(db._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        assert journal == "delete"
        fk = int(db._conn.execute("PRAGMA foreign_keys").fetchone()[0])
        assert fk == 1
    finally:
        db.close()


# ── Future / partial / missing-column shapes fail closed ──


def test_future_version_refuses_to_open(tmp_path) -> None:
    path = str(tmp_path / "future.db")
    db = Database(db_path=path)
    db.close()
    con = sqlite3.connect(path)
    con.execute(
        "UPDATE schema_version SET version = ? WHERE id = 1",
        (CURRENT_SCHEMA_VERSION + 1,),
    )
    con.commit()
    con.close()
    with pytest.raises(DatabaseSchemaError, match="newer than this build"):
        Database(db_path=path)


def test_stamped_older_version_refuses_to_migrate(tmp_path) -> None:
    path = str(tmp_path / "old_stamped.db")
    db = Database(db_path=path)
    db.close()
    con = sqlite3.connect(path)
    con.execute(
        "UPDATE schema_version SET version = 1 WHERE id = 1",
    )
    con.commit()
    con.close()
    with pytest.raises(DatabaseSchemaError, match="predates all validated"):
        Database(db_path=path)


def test_partial_schema_fails_closed(tmp_path) -> None:
    path = str(tmp_path / "partial.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    with pytest.raises(DatabaseSchemaError):
        Database(db_path=path)


def test_missing_column_fails_closed(tmp_path) -> None:
    path = str(tmp_path / "badcol.db")
    con = sqlite3.connect(path)
    con.executescript(LEGACY_V1_SCHEMA)
    con.execute("CREATE TABLE risk_state (symbol TEXT NOT NULL)")
    con.commit()
    con.close()
    with pytest.raises(DatabaseSchemaError, match="missing expected columns"):
        Database(db_path=path)


def test_empty_schema_version_table_rederived(tmp_path) -> None:
    """An existing-but-empty stamp table is re-derived, never trusted."""
    path = str(tmp_path / "emptystamp.db")
    make_unstamped_v2(path)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, "
        "version INTEGER NOT NULL, migrated_at TEXT NOT NULL)"
    )
    con.commit()
    con.close()
    db = Database(db_path=path)
    try:
        assert db.get_schema_version() == CURRENT_SCHEMA_VERSION
        assert read_schema_version(db._conn) == CURRENT_SCHEMA_VERSION
        row = db.load_risk_state("BTC", "paper_trade", "2026-10-23")
        assert row is not None and row["kill_engaged"] == 1
    finally:
        db.close()


def test_unrelated_only_database_fails_closed(tmp_path) -> None:
    path = str(tmp_path / "foreign.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE whatever (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    with pytest.raises(DatabaseSchemaError, match="Unrecognized database"):
        Database(db_path=path)
