from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.db_migrations import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_V1,
    LEGACY_V2_UNSTAMPED,
    DatabaseSchemaError,
    classify_legacy_schema,
    read_schema_version,
    verify_complete_schema,
)

logger = logging.getLogger(__name__)

# P6-verified runtime settings. DELETE journal mode is a deliberate choice
# (single local file, NFS/streaming-backup-hostile WAL sidecars avoided);
# foreign_keys=ON is enforcement-only — P6 declares no new REFERENCES
# constraints and changes no table relationships. synchronous=FULL is
# deliberate: this is a low-write-rate risk database, so the per-commit
# fsync cost is negligible while the guarantee — a committed
# save_risk_state (including a latched kill) survives process crash, OS
# crash, and power loss — is exactly what the kill-switch contract
# ("restarts never clear a kill") presupposes. NORMAL would retain only
# process-crash safety and could leave a torn file after OS crash/power
# loss. busy_timeout avoids immediate SQLITE_BUSY on brief contention.
# _verify_pragmas() asserts all four after setting them and raises
# DatabaseSchemaError (fail closed) on any mismatch.
_EXPECTED_JOURNAL_MODE = "delete"
_EXPECTED_SYNCHRONOUS = "FULL"
_BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
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

CREATE TABLE IF NOT EXISTS orders (
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

CREATE TABLE IF NOT EXISTS fills (
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

CREATE TABLE IF NOT EXISTS trades (
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

CREATE TABLE IF NOT EXISTS analysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_type TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_id);
CREATE INDEX IF NOT EXISTS idx_fills_session ON fills(session_id);
CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_analysis_type ON analysis_results(result_type);
CREATE INDEX IF NOT EXISTS idx_analysis_strategy ON analysis_results(strategy_name);

CREATE TABLE IF NOT EXISTS risk_state (
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

CREATE INDEX IF NOT EXISTS idx_risk_state_day ON risk_state(day);

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    migrated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize(obj: Any) -> Any:
    """
    Recursively converts dataclasses (including nested ones, e.g.
    OutOfSampleReport containing BacktestResult/PerformanceReport) to
    plain dicts/lists for JSON storage. Falls through to json.dumps'
    own default=str handling for anything else it doesn't recognize.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _require_lastrowid(cur: sqlite3.Cursor) -> int:
    """
    sqlite3.Cursor.lastrowid is typed as int | None (it's None for
    cursors that never executed an INSERT) — for every INSERT in this
    module it's always a real int immediately after execute(), but
    that fact isn't visible to mypy from the stub alone. This narrows
    it explicitly with a clear error instead of an int(None) TypeError
    if that assumption is ever wrong.
    """
    if cur.lastrowid is None:
        raise RuntimeError("INSERT did not return a row id (lastrowid is None).")
    return cur.lastrowid


class Database:
    """
    SQLite-backed persistence for Hyper-Engine. One file, safe for
    single-process concurrent use (guarded by an internal lock — this
    is an embedded local database, not a multi-writer server).

    Usage:
        db = Database()  # defaults to storage/hyper_engine.db
        db.save_session_start(session_id="s1", symbol="BTC", strategy="EMA(9,21)", mode="paper", starting_balance=10_000.0)
        db.save_fill(session_id="s1", symbol="BTC", side="BUY", price=50000.0, size=0.001)
        db.save_backtest_result("EMA(9,21)", "BTC", result, report)
        db.list_trades(symbol="BTC", limit=20)
    """

    def __init__(self, db_path: str = "storage/hyper_engine.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            with self._lock, self._conn:
                self._apply_pragmas()
                self._run_migrations()
                self._verify_pragmas()
        except Exception:
            # The connection is already open at this point — close it
            # before propagating so a failed open never leaks a handle
            # (or a Windows file lock) that would block the restore the
            # error message tells the operator to perform. close() itself
            # must never mask the original failure.
            try:
                self._conn.close()
            except Exception as close_error:
                logger.warning(
                    "Error closing database connection during failed "
                    "initialization of %s: %s",
                    self.db_path,
                    close_error,
                )
            raise
        logger.info("Database initialized at %s", self.db_path)

    # ──────────────────────────────────────────────────────────────
    # P6: PRAGMAS + VERSIONED MIGRATIONS (additive, non-destructive)
    # ──────────────────────────────────────────────────────────────
    # No DROP TABLE, no column removal, no data rewrite: migrations only
    # add the missing tables/indexes and stamp the version. Unknown
    # future versions and unrecognized shapes raise DatabaseSchemaError,
    # which propagates out of __init__ — the paper/live/smoke entry
    # points already refuse to trade when Database() raises.

    def _apply_pragmas(self) -> None:
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def _verify_pragmas(self) -> None:
        journal = str(self._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if journal != _EXPECTED_JOURNAL_MODE:
            raise DatabaseSchemaError(
                f"journal_mode is '{journal}', expected "
                f"'{_EXPECTED_JOURNAL_MODE}' — refusing to trade on an "
                "unverified storage configuration."
            )
        sync_row = self._conn.execute("PRAGMA synchronous").fetchone()[0]
        sync_name = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}.get(
            int(sync_row), str(sync_row)
        )
        if sync_name != _EXPECTED_SYNCHRONOUS:
            raise DatabaseSchemaError(
                f"synchronous is '{sync_name}', expected "
                f"'{_EXPECTED_SYNCHRONOUS}' — refusing to trade on an "
                "unverified storage configuration."
            )
        fk = int(self._conn.execute("PRAGMA foreign_keys").fetchone()[0])
        if fk != 1:
            raise DatabaseSchemaError(
                "foreign_keys enforcement is OFF — refusing to trade "
                "without the verified integrity setting."
            )
        timeout = int(self._conn.execute("PRAGMA busy_timeout").fetchone()[0])
        if timeout != _BUSY_TIMEOUT_MS:
            raise DatabaseSchemaError(
                f"busy_timeout is {timeout}ms, expected {_BUSY_TIMEOUT_MS}ms."
            )

    def _run_migrations(self) -> None:
        """Detects the on-disk shape and stamps/migrates it to current.

        Crash safety comes from idempotence, not DDL atomicity: every
        statement below is CREATE TABLE/INDEX IF NOT EXISTS or an
        UPSERT stamp, so a crash mid-migration simply re-runs the
        remaining additive work on the next open. SQLite DDL atomicity
        beyond that is not assumed.

        Steps:
        1. Read the schema_version stamp (if any).
        2. Fresh (no tables): create the full current schema, stamp v2.
        3. Stamped + current: verify, no-op.
        4. Stamped + future: fail closed (never downgrade silently).
        5. Stamped + older: fail closed — P6 ships no down-migration
           path because none has been validated (v1 shipped without
           version tracking, so no stamped-v1 file exists in the wild).
        6. Unstamped + legacy shape: classify by inspection (v1 five
           tables / v2 six tables), apply the canonical DDL additively
           (IF NOT EXISTS — every existing row preserved), then stamp.
        """
        version = read_schema_version(self._conn)
        if version is None:
            legacy = classify_legacy_schema(self._conn)
            if legacy is None:
                self._conn.executescript(_SCHEMA)  # fresh database
                self._stamp_version(CURRENT_SCHEMA_VERSION)
                logger.info(
                    "Fresh database created at schema v%s", CURRENT_SCHEMA_VERSION
                )
                return
            if legacy == LEGACY_V2_UNSTAMPED:
                self._conn.executescript(_SCHEMA)  # adds schema_version only
                self._stamp_version(CURRENT_SCHEMA_VERSION)
                logger.info(
                    "Migrated unstamped risk_state-era database to schema v%s "
                    "(additive: version tracking only, all rows preserved)",
                    CURRENT_SCHEMA_VERSION,
                )
                return
            if legacy == LEGACY_V1:
                self._conn.executescript(_SCHEMA)  # adds risk_state + version
                self._stamp_version(CURRENT_SCHEMA_VERSION)
                logger.info(
                    "Migrated legacy v1 database to schema v%s "
                    "(additive: risk_state + version tracking, "
                    "all existing rows preserved)",
                    CURRENT_SCHEMA_VERSION,
                )
                return
            raise DatabaseSchemaError(  # pragma: no cover — defensive
                "Unrecognized legacy classification — refusing to migrate."
            )
        if version == CURRENT_SCHEMA_VERSION:
            verify_complete_schema(self._conn)  # fail closed on torn copies
            self._conn.executescript(_SCHEMA)  # idempotent index repair only
            self._stamp_version(CURRENT_SCHEMA_VERSION)
            return
        if version > CURRENT_SCHEMA_VERSION:
            raise DatabaseSchemaError(
                f"Database schema v{version} is newer than this build "
                f"(v{CURRENT_SCHEMA_VERSION}) — refusing to open a database "
                "this code cannot understand. Upgrade Hyper-Engine first."
            )
        raise DatabaseSchemaError(
            f"Database schema v{version} predates all validated migration "
            "paths — refusing to migrate an unknown stamped version. "
            "Restore from backup instead."
        )

    def _stamp_version(self, version: int) -> None:
        self._conn.execute(
            "INSERT INTO schema_version (id, version, migrated_at) "
            "VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET version=excluded.version, "
            "migrated_at=excluded.migrated_at",
            (version, _now_iso()),
        )

    def get_schema_version(self) -> int:
        """Stamped schema version. Raises if unstamped (should not happen
        after __init__ — its presence proves the value)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT version FROM schema_version WHERE id = 1"
            ).fetchone()
        if row is None:
            raise DatabaseSchemaError(
                "schema_version row missing after initialization."
            )
        return int(row[0])

    def backup_to(self, dest: str | Path) -> None:
        """Copies the database to `dest` via SQLite's online backup API.

        Never copy a live database file with a plain file copy (stale
        reads, torn pages, WAL sidecars). The backup API reads through a
        consistent snapshot while the source stays open. Destination
        parent directories are created; an existing destination file is
        overwritten in place by SQLite.

        Raises DatabaseSchemaError if `dest` resolves to the source
        database itself — backing a database onto itself is rejected
        before any connection is opened. A failed backup leaves the
        source untouched; see docs/database.md before trusting a
        pre-existing destination file after a failure.
        """
        dest_path = Path(dest)
        try:
            same = os.path.samefile(dest_path, self.db_path)
        except OSError:
            same = dest_path.resolve() == self.db_path.resolve()
        if same:
            raise DatabaseSchemaError(
                f"Refusing to back up database {self.db_path} onto itself."
            )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            target = sqlite3.connect(str(dest_path))
            try:
                self._conn.backup(target)
            finally:
                target.close()
        logger.info("Database backed up to %s", dest_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ──────────────────────────────────────────────────────────────
    # SESSIONS
    # ──────────────────────────────────────────────────────────────

    def save_session_start(
        self,
        session_id: str,
        symbol: str,
        strategy: str,
        mode: str,
        starting_balance: float,
    ) -> None:
        """
        Inserts a new session row, or is a no-op if session_id already
        exists (INSERT OR IGNORE) — safe to call defensively without
        needing to check existence first.
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO sessions
                    (session_id, symbol, strategy, mode, started_at, starting_balance)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, symbol, strategy, mode, _now_iso(), starting_balance),
            )

    def save_session_end(
        self,
        session_id: str,
        ending_balance: float,
        total_pnl: float,
        num_trades: int,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE sessions
                SET ended_at = ?, ending_balance = ?, total_pnl = ?, num_trades = ?
                WHERE session_id = ?
                """,
                (_now_iso(), ending_balance, total_pnl, num_trades, session_id),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return _row_to_dict(row)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────────────────────
    # ORDERS / FILLS / TRADES
    # ──────────────────────────────────────────────────────────────

    def save_order(
        self,
        session_id: str,
        symbol: str,
        side: str,
        price: float,
        size: float,
        order_type: str = "MARKET",
        oid: int | None = None,
        cloid: str | None = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO orders
                    (session_id, symbol, side, price, size, order_type, oid, cloid, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    symbol,
                    side,
                    price,
                    size,
                    order_type,
                    oid,
                    cloid,
                    _now_iso(),
                ),
            )
            return _require_lastrowid(cur)

    def save_fill(
        self,
        session_id: str,
        symbol: str,
        side: str,
        price: float,
        size: float,
        fee: float = 0.0,
        oid: int | None = None,
        cloid: str | None = None,
    ) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO fills
                    (session_id, symbol, side, price, size, fee, oid, cloid, filled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, symbol, side, price, size, fee, oid, cloid, _now_iso()),
            )
            return _require_lastrowid(cur)

    def save_trade(
        self,
        session_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        size: float,
        pnl: float,
        pnl_pct: float,
        entry_time: str | None = None,
        exit_time: str | None = None,
    ) -> int:
        """Persists one completed round-trip trade (entry + exit)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO trades
                    (session_id, symbol, side, entry_price, exit_price, size,
                     pnl, pnl_pct, entry_time, exit_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    symbol,
                    side,
                    entry_price,
                    exit_price,
                    size,
                    pnl,
                    pnl_pct,
                    entry_time,
                    exit_time or _now_iso(),
                ),
            )
            return _require_lastrowid(cur)

    def list_orders(
        self, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self._list("orders", "created_at", session_id=session_id, limit=limit)

    def list_fills(
        self, session_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self._list("fills", "filled_at", session_id=session_id, limit=limit)

    def list_trades(
        self,
        session_id: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM trades {where} ORDER BY exit_time DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def _list(
        self,
        table: str,
        order_col: str,
        session_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if session_id is not None:
            query = f"SELECT * FROM {table} WHERE session_id = ? ORDER BY {order_col} DESC LIMIT ?"
            params: tuple = (session_id, limit)
        else:
            query = f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?"
            params = (limit,)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────────────────────────
    # ANALYSIS RESULTS (backtest / OOS / walk-forward / Monte Carlo)
    # ──────────────────────────────────────────────────────────────

    def save_analysis_result(
        self,
        result_type: str,
        strategy_name: str,
        symbol: str,
        result: Any,
    ) -> int:
        """
        Persists any analysis result as JSON. Accepts a dataclass (or
        nested dataclasses — recursively serialized), a plain dict, or
        anything else json.dumps can handle with default=str.

        Args:
            result_type: e.g. "backtest", "out_of_sample",
                         "walk_forward", "monte_carlo" — free-form, but
                         keep it consistent for querying via
                         list_analysis_results(result_type=...).
            strategy_name: e.g. "EMA(9,21)".
            symbol: e.g. "BTC".
            result: the object to persist.

        Returns:
            The new row's id.
        """
        summary_json = json.dumps(_serialize(result), default=str)
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO analysis_results
                    (result_type, strategy_name, symbol, created_at, summary_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (result_type, strategy_name, symbol, _now_iso(), summary_json),
            )
            return _require_lastrowid(cur)

    def get_analysis_result(self, result_id: int) -> dict[str, Any] | None:
        """Returns the row plus `summary` (the parsed JSON dict), or
        None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM analysis_results WHERE id = ?", (result_id,)
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["summary"] = json.loads(data["summary_json"])
        return data

    def list_analysis_results(
        self,
        result_type: str | None = None,
        strategy_name: str | None = None,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Lists analysis results (metadata only — summary_json is NOT
        parsed here, for speed when listing many rows; use
        get_analysis_result(id) for the full parsed summary).
        """
        clauses = []
        params: list[Any] = []
        if result_type is not None:
            clauses.append("result_type = ?")
            params.append(result_type)
        if strategy_name is not None:
            clauses.append("strategy_name = ?")
            params.append(strategy_name)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, result_type, strategy_name, symbol, created_at "
                f"FROM analysis_results {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Typed convenience wrappers (thin, mypy-checked call sites) ──

    def save_backtest_result(
        self, strategy_name: str, symbol: str, result: Any, report: Any
    ) -> int:
        """Persists a Backtester.run() result + its PerformanceReport together."""
        return self.save_analysis_result(
            "backtest",
            strategy_name,
            symbol,
            {"result": result, "report": report},
        )

    def save_out_of_sample_result(self, symbol: str, oos_report: Any) -> int:
        """Persists an OutOfSampleReport (from run_out_of_sample_test())."""
        return self.save_analysis_result(
            "out_of_sample", oos_report.strategy_name, symbol, oos_report
        )

    def save_walk_forward_result(
        self, strategy_name: str, symbol: str, wf_report: Any
    ) -> int:
        """Persists a WalkForwardReport (from run_walk_forward_test())."""
        return self.save_analysis_result(
            "walk_forward", strategy_name, symbol, wf_report
        )

    def save_monte_carlo_result(
        self, strategy_name: str, symbol: str, mc_report: Any
    ) -> int:
        """Persists a MonteCarloReport (from run_monte_carlo_simulation())."""
        return self.save_analysis_result(
            "monte_carlo", strategy_name, symbol, mc_report
        )

    # ──────────────────────────────────────────────────────────────
    # RISK STATE (persistent risk counters, position intent, kill flag)
    # ──────────────────────────────────────────────────────────────
    # Additive only: this table did not exist before P5, so there is no
    # legacy data to migrate and no existing query to disturb. One row
    # per (symbol, mode, day); UPSERT keeps crash-restart idempotent.

    def save_risk_state(
        self,
        symbol: str,
        mode: str,
        day: str,
        session_id: str,
        realized_loss: float = 0.0,
        open_qty: float = 0.0,
        entry_price: float = 0.0,
        entry_fee: float = 0.0,
        basis_known: bool = False,
        total_pnl: float = 0.0,
        num_trades: int = 0,
        kill_engaged: bool = False,
        kill_reason: str | None = None,
    ) -> None:
        """Inserts or replaces the risk-state row for (symbol, mode, day).

        Single statement = atomic under the module's lock/transaction
        discipline. Never clears a kill flag implicitly: callers pass
        the flag through from their in-memory state.
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO risk_state
                    (symbol, mode, day, session_id, realized_loss, open_qty,
                     entry_price, entry_fee, basis_known, total_pnl, num_trades,
                     kill_engaged, kill_reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, mode, day) DO UPDATE SET
                    session_id = excluded.session_id,
                    realized_loss = excluded.realized_loss,
                    open_qty = excluded.open_qty,
                    entry_price = excluded.entry_price,
                    entry_fee = excluded.entry_fee,
                    basis_known = excluded.basis_known,
                    total_pnl = excluded.total_pnl,
                    num_trades = excluded.num_trades,
                    kill_engaged = excluded.kill_engaged,
                    kill_reason = excluded.kill_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    mode,
                    day,
                    session_id,
                    realized_loss,
                    open_qty,
                    entry_price,
                    entry_fee,
                    int(basis_known),
                    total_pnl,
                    num_trades,
                    int(kill_engaged),
                    kill_reason,
                    _now_iso(),
                ),
            )

    def load_risk_state(
        self, symbol: str, mode: str, day: str
    ) -> dict[str, Any] | None:
        """Returns the risk-state row for (symbol, mode, day), or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM risk_state WHERE symbol = ? AND mode = ? AND day = ?",
                (symbol, mode, day),
            ).fetchone()
        return _row_to_dict(row)

    def load_latest_risk_state_before(
        self, symbol: str, mode: str, day: str
    ) -> dict[str, Any] | None:
        """Returns the most recent risk-state row for (symbol, mode) dated
        strictly before `day` (YYYY-MM-DD compares chronologically), or
        None. Read-only carry-forward lookup for position recovery across
        UTC rollovers — never writes."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM risk_state
                WHERE symbol = ? AND mode = ? AND day < ?
                ORDER BY day DESC LIMIT 1
                """,
                (symbol, mode, day),
            ).fetchone()
        return _row_to_dict(row)

    def clear_risk_kill(self, symbol: str, mode: str, day: str) -> None:
        """Clears only the kill flag/reason, preserving all counters.

        Operator use only (see core.risk_state for the full reset
        procedure). Never called automatically by trading loops.
        """
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE risk_state
                SET kill_engaged = 0, kill_reason = NULL, updated_at = ?
                WHERE symbol = ? AND mode = ? AND day = ?
                """,
                (_now_iso(), symbol, mode, day),
            )
