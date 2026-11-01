"""Versioned SQLite schema migrations (P6).

Non-destructive only: migrations add tables/indexes and stamp the schema
version; they never drop tables, remove columns, rewrite data files, or
clear risk-state information (kill flags, reasons, counters, PnL, intent).

Version history (grounded in git history, not assumed):
- v1: persistence layer as committed in 7135edd — sessions, orders,
  fills, trades, analysis_results. No risk_state, no version tracking.
- v2 (current): v1 + risk_state table (P5 working-tree addition) +
  schema_version tracking (P6). Fresh and legacy databases are
  distinguished by inspecting sqlite_master + PRAGMA table_info
  BEFORE any DDL runs — never by assuming.

Unknown future versions and unrecognized/partial schemas fail closed
via DatabaseSchemaError, which Database.__init__ lets propagate so
trading loops refuse to start.
"""

from __future__ import annotations

import sqlite3

from core.exceptions import HyperEngineError


CURRENT_SCHEMA_VERSION = 2

# Legacy markers returned by classify_legacy_schema().
LEGACY_V1 = 1  # five tables, no risk_state
LEGACY_V2_UNSTAMPED = 2  # all six tables, predates schema_version


class DatabaseSchemaError(HyperEngineError):
    """Raised when the database schema cannot be migrated safely.

    Propagates out of Database.__init__ so paper/live/smoke entry
    points — which already refuse to trade when Database() raises —
    fail closed without any order-boundary changes.
    """


# Application tables per era. Anything else in the file is ignored for
# classification (but never touched by migrations).
APP_TABLES_V1 = frozenset({"sessions", "orders", "fills", "trades", "analysis_results"})
APP_TABLES_V2 = APP_TABLES_V1 | {"risk_state"}

# Columns each table must at least have (extras are tolerated, missing
# columns fail closed). Mirrors the canonical DDL in core.database.
EXPECTED_COLUMNS: dict[str, frozenset[str]] = {
    "sessions": frozenset(
        {
            "session_id",
            "symbol",
            "strategy",
            "mode",
            "started_at",
            "ended_at",
            "starting_balance",
            "ending_balance",
            "total_pnl",
            "num_trades",
        }
    ),
    "orders": frozenset(
        {
            "id",
            "session_id",
            "symbol",
            "side",
            "price",
            "size",
            "order_type",
            "oid",
            "cloid",
            "created_at",
        }
    ),
    "fills": frozenset(
        {
            "id",
            "session_id",
            "symbol",
            "side",
            "price",
            "size",
            "fee",
            "oid",
            "cloid",
            "filled_at",
        }
    ),
    "trades": frozenset(
        {
            "id",
            "session_id",
            "symbol",
            "side",
            "entry_price",
            "exit_price",
            "size",
            "pnl",
            "pnl_pct",
            "entry_time",
            "exit_time",
        }
    ),
    "analysis_results": frozenset(
        {
            "id",
            "result_type",
            "strategy_name",
            "symbol",
            "created_at",
            "summary_json",
        }
    ),
    "risk_state": frozenset(
        {
            "symbol",
            "mode",
            "day",
            "session_id",
            "realized_loss",
            "open_qty",
            "entry_price",
            "entry_fee",
            "basis_known",
            "total_pnl",
            "num_trades",
            "kill_engaged",
            "kill_reason",
            "updated_at",
        }
    ),
}


def get_user_tables(conn: sqlite3.Connection) -> set[str]:
    """Names of all user tables (excludes SQLite internals)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(r[0]) for r in rows}


def get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names for one table. Callers pass internal constants only."""
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def verify_complete_schema(conn: sqlite3.Connection) -> None:
    """Fails closed unless every expected table and column is present.

    Used for stamped-current databases: IF NOT EXISTS DDL must never
    silently paper over a torn/corrupt copy by recreating an empty
    table. Extras (user or tool tables) are tolerated; anything missing
    raises DatabaseSchemaError.
    """
    tables = get_user_tables(conn)
    missing_tables = set(APP_TABLES_V2) - tables
    if missing_tables:
        raise DatabaseSchemaError(
            "Database stamped current but missing tables "
            f"{sorted(missing_tables)} — refusing to trade on a "
            "possibly torn copy. Restore from backup instead."
        )
    for table in sorted(APP_TABLES_V2):
        missing = set(EXPECTED_COLUMNS[table]) - get_table_columns(conn, table)
        if missing:
            raise DatabaseSchemaError(
                f"Table '{table}' is missing expected columns "
                f"{sorted(missing)} — refusing to trade on a database "
                "whose shape does not match its stamped version."
            )


def read_schema_version(conn: sqlite3.Connection) -> int | None:
    """Stamped version, or None when unstamped (fresh or legacy).

    An existing-but-empty schema_version table also reads as None so a
    half-written stamp is re-derived from the actual schema, never trusted.
    """
    if "schema_version" not in get_user_tables(conn):
        return None
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return int(row[0])


def classify_legacy_schema(conn: sqlite3.Connection) -> int | None:
    """Classifies an UNSTAMPED database by inspecting its actual shape.

    Returns None for a fresh database (no tables at all), LEGACY_V1 for
    the five-table 7135edd shape, LEGACY_V2_UNSTAMPED for the six-table
    risk_state-era shape. Raises DatabaseSchemaError for anything else
    (partial app schema, missing columns, or unrecognized tables only) —
    fail closed, never guess.

    Must be called BEFORE any DDL runs so the evidence is intact.
    """
    tables = get_user_tables(conn)
    tables.discard("schema_version")  # empty stamp table carries no signal
    present = tables & set(APP_TABLES_V2)
    if not present:
        if tables:
            raise DatabaseSchemaError(
                "Unrecognized database: no Hyper-Engine tables found, "
                f"existing tables={sorted(tables)}. Refusing to migrate."
            )
        return None
    for table in sorted(present):
        missing = set(EXPECTED_COLUMNS[table]) - get_table_columns(conn, table)
        if missing:
            raise DatabaseSchemaError(
                f"Table '{table}' is missing expected columns "
                f"{sorted(missing)} — refusing to migrate a database "
                "whose shape does not match any known version."
            )
    if APP_TABLES_V1 <= tables:
        if "risk_state" in tables:
            return LEGACY_V2_UNSTAMPED
        return LEGACY_V1
    raise DatabaseSchemaError(
        "Partial legacy schema: present app tables do not match any known "
        f"version (found={sorted(present)}). Refusing to migrate."
    )
