import json

import pytest

from backtester.backtester import BacktestResult, Trade
from backtester.monte_carlo import MonteCarloReport, MonteCarloSimulationResult
from backtester.out_of_sample import OutOfSampleReport, OutOfSampleSplit
from backtester.performance import PerformanceAnalyser
from backtester.walk_forward import (
    WalkForwardReport,
    WalkForwardWindow,
    WalkForwardWindowResult,
)
from core.database import Database


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_hyper_engine.db")


@pytest.fixture
def db(db_path):
    database = Database(db_path=db_path)
    yield database
    database.close()


# ──────────────────────────────────────────────────────────────
# INIT / SCHEMA
# ──────────────────────────────────────────────────────────────


def test_creates_db_file(db, db_path):
    from pathlib import Path

    assert Path(db_path).exists()


def test_creates_parent_directory(tmp_path):
    nested_path = str(tmp_path / "nested" / "dir" / "test.db")
    database = Database(db_path=nested_path)
    from pathlib import Path

    assert Path(nested_path).exists()
    database.close()


def test_reopening_existing_db_does_not_error(db_path):
    db1 = Database(db_path=db_path)
    db1.close()
    db2 = Database(db_path=db_path)  # must not raise on existing schema
    db2.close()


def test_context_manager_closes_connection(db_path):
    with Database(db_path=db_path) as database:
        database.save_session_start(
            session_id="s1",
            symbol="BTC",
            strategy="EMA",
            mode="paper",
            starting_balance=10_000.0,
        )
    # Connection closed after the with-block; a fresh connection can reopen it.
    with Database(db_path=db_path) as database2:
        assert database2.get_session("s1") is not None


# ──────────────────────────────────────────────────────────────
# SESSIONS
# ──────────────────────────────────────────────────────────────


def test_save_and_get_session_start(db):
    db.save_session_start(
        session_id="s1",
        symbol="BTC",
        strategy="EMA(9,21)",
        mode="paper",
        starting_balance=10_000.0,
    )
    session = db.get_session("s1")
    assert session is not None
    assert session["symbol"] == "BTC"
    assert session["strategy"] == "EMA(9,21)"
    assert session["mode"] == "paper"
    assert session["starting_balance"] == 10_000.0
    assert session["ended_at"] is None


def test_get_nonexistent_session_returns_none(db):
    assert db.get_session("does-not-exist") is None


def test_save_session_start_twice_does_not_raise_or_duplicate(db):
    """INSERT OR IGNORE: calling save_session_start twice with the
    same session_id must not raise or duplicate the row."""
    db.save_session_start(
        session_id="s1",
        symbol="BTC",
        strategy="EMA",
        mode="paper",
        starting_balance=10_000.0,
    )
    db.save_session_start(
        session_id="s1",
        symbol="ETH",
        strategy="RSI",
        mode="testnet_live",
        starting_balance=5_000.0,
    )  # must not raise
    sessions = db.list_sessions()
    matching = [s for s in sessions if s["session_id"] == "s1"]
    assert len(matching) == 1
    # First write wins (INSERT OR IGNORE) — original values preserved.
    assert matching[0]["symbol"] == "BTC"


def test_save_session_end_updates_existing_session(db):
    db.save_session_start(
        session_id="s1",
        symbol="BTC",
        strategy="EMA",
        mode="paper",
        starting_balance=10_000.0,
    )
    db.save_session_end(
        session_id="s1", ending_balance=10_500.0, total_pnl=500.0, num_trades=3
    )
    session = db.get_session("s1")
    assert session["ending_balance"] == 10_500.0
    assert session["total_pnl"] == 500.0
    assert session["num_trades"] == 3
    assert session["ended_at"] is not None


def test_list_sessions_returns_most_recent_first(db):
    db.save_session_start(
        session_id="s1",
        symbol="BTC",
        strategy="EMA",
        mode="paper",
        starting_balance=10_000.0,
    )
    db.save_session_start(
        session_id="s2",
        symbol="BTC",
        strategy="RSI",
        mode="paper",
        starting_balance=10_000.0,
    )
    sessions = db.list_sessions()
    assert len(sessions) == 2
    ids = [s["session_id"] for s in sessions]
    assert set(ids) == {"s1", "s2"}


def test_list_sessions_respects_limit(db):
    for i in range(5):
        db.save_session_start(
            session_id=f"s{i}",
            symbol="BTC",
            strategy="EMA",
            mode="paper",
            starting_balance=10_000.0,
        )
    assert len(db.list_sessions(limit=3)) == 3


# ──────────────────────────────────────────────────────────────
# ORDERS
# ──────────────────────────────────────────────────────────────


def test_save_order_returns_id(db):
    order_id = db.save_order(
        session_id="s1", symbol="BTC", side="BUY", price=50_000.0, size=0.001
    )
    assert isinstance(order_id, int)
    assert order_id > 0


def test_list_orders_returns_saved_order(db):
    db.save_order(
        session_id="s1",
        symbol="BTC",
        side="BUY",
        price=50_000.0,
        size=0.001,
        order_type="MARKET",
        oid=123,
        cloid="abc",
    )
    orders = db.list_orders()
    assert len(orders) == 1
    assert orders[0]["side"] == "BUY"
    assert orders[0]["price"] == 50_000.0
    assert orders[0]["oid"] == 123
    assert orders[0]["cloid"] == "abc"


def test_list_orders_filters_by_session(db):
    db.save_order(session_id="s1", symbol="BTC", side="BUY", price=50_000.0, size=0.001)
    db.save_order(
        session_id="s2", symbol="BTC", side="SELL", price=51_000.0, size=0.001
    )
    orders = db.list_orders(session_id="s1")
    assert len(orders) == 1
    assert orders[0]["session_id"] == "s1"


# ──────────────────────────────────────────────────────────────
# FILLS
# ──────────────────────────────────────────────────────────────


def test_save_and_list_fill(db):
    db.save_fill(
        session_id="s1",
        symbol="BTC",
        side="BUY",
        price=50_050.0,
        size=0.001,
        fee=0.05,
        oid=456,
    )
    fills = db.list_fills()
    assert len(fills) == 1
    assert fills[0]["price"] == 50_050.0
    assert fills[0]["fee"] == 0.05
    assert fills[0]["oid"] == 456


def test_list_fills_filters_by_session(db):
    db.save_fill(session_id="s1", symbol="BTC", side="BUY", price=50_000.0, size=0.001)
    db.save_fill(session_id="s2", symbol="BTC", side="BUY", price=51_000.0, size=0.001)
    fills = db.list_fills(session_id="s2")
    assert len(fills) == 1
    assert fills[0]["session_id"] == "s2"


# ──────────────────────────────────────────────────────────────
# TRADES
# ──────────────────────────────────────────────────────────────


def test_save_and_list_trade(db):
    db.save_trade(
        session_id="s1",
        symbol="BTC",
        side="LONG",
        entry_price=50_000.0,
        exit_price=51_000.0,
        size=0.001,
        pnl=1.0,
        pnl_pct=0.02,
    )
    trades = db.list_trades()
    assert len(trades) == 1
    assert trades[0]["pnl"] == 1.0
    assert trades[0]["entry_price"] == 50_000.0
    assert trades[0]["exit_price"] == 51_000.0


def test_list_trades_filters_by_symbol(db):
    db.save_trade(
        session_id="s1",
        symbol="BTC",
        side="LONG",
        entry_price=50_000.0,
        exit_price=51_000.0,
        size=0.001,
        pnl=1.0,
        pnl_pct=0.02,
    )
    db.save_trade(
        session_id="s1",
        symbol="ETH",
        side="LONG",
        entry_price=3_000.0,
        exit_price=3_100.0,
        size=0.01,
        pnl=1.0,
        pnl_pct=0.033,
    )
    trades = db.list_trades(symbol="BTC")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "BTC"


def test_list_trades_filters_by_session_and_symbol_together(db):
    db.save_trade(
        session_id="s1",
        symbol="BTC",
        side="LONG",
        entry_price=50_000.0,
        exit_price=51_000.0,
        size=0.001,
        pnl=1.0,
        pnl_pct=0.02,
    )
    db.save_trade(
        session_id="s2",
        symbol="BTC",
        side="LONG",
        entry_price=50_000.0,
        exit_price=49_000.0,
        size=0.001,
        pnl=-1.0,
        pnl_pct=-0.02,
    )
    trades = db.list_trades(session_id="s1", symbol="BTC")
    assert len(trades) == 1
    assert trades[0]["pnl"] == 1.0


def test_list_trades_respects_limit(db):
    for i in range(5):
        db.save_trade(
            session_id="s1",
            symbol="BTC",
            side="LONG",
            entry_price=100.0,
            exit_price=101.0,
            size=1.0,
            pnl=1.0,
            pnl_pct=0.01,
        )
    assert len(db.list_trades(limit=2)) == 2


# ──────────────────────────────────────────────────────────────
# ANALYSIS RESULTS — GENERIC
# ──────────────────────────────────────────────────────────────


def test_save_and_get_analysis_result_plain_dict(db):
    result_id = db.save_analysis_result(
        "custom", "EMA(9,21)", "BTC", {"total_pnl": 100.0, "sharpe": 1.5}
    )
    stored = db.get_analysis_result(result_id)
    assert stored is not None
    assert stored["result_type"] == "custom"
    assert stored["strategy_name"] == "EMA(9,21)"
    assert stored["symbol"] == "BTC"
    assert stored["summary"]["total_pnl"] == 100.0
    assert stored["summary"]["sharpe"] == 1.5


def test_get_nonexistent_analysis_result_returns_none(db):
    assert db.get_analysis_result(99999) is None


def test_list_analysis_results_filters_by_type(db):
    db.save_analysis_result("backtest", "EMA", "BTC", {"a": 1})
    db.save_analysis_result("monte_carlo", "EMA", "BTC", {"b": 2})
    results = db.list_analysis_results(result_type="backtest")
    assert len(results) == 1
    assert results[0]["result_type"] == "backtest"


def test_list_analysis_results_filters_by_strategy_and_symbol(db):
    db.save_analysis_result("backtest", "EMA", "BTC", {"a": 1})
    db.save_analysis_result("backtest", "RSI", "BTC", {"a": 1})
    db.save_analysis_result("backtest", "EMA", "ETH", {"a": 1})
    results = db.list_analysis_results(strategy_name="EMA", symbol="BTC")
    assert len(results) == 1


def test_list_analysis_results_does_not_include_full_summary(db):
    """list_analysis_results is metadata-only for speed — summary_json
    is not parsed there; use get_analysis_result(id) for the full
    parsed summary."""
    db.save_analysis_result("backtest", "EMA", "BTC", {"a": 1})
    results = db.list_analysis_results()
    assert "summary" not in results[0]


# ──────────────────────────────────────────────────────────────
# ANALYSIS RESULTS — REAL DATACLASS TYPES (serialization correctness)
# ──────────────────────────────────────────────────────────────


def make_backtest_result():
    trade = Trade(
        entry_time=1000,
        exit_time=2000,
        entry_price=100.0,
        exit_price=105.0,
        size=1.0,
        pnl=5.0,
        pnl_pct=0.05,
    )
    return BacktestResult(
        trades=[trade],
        total_pnl=5.0,
        win_rate=1.0,
        profit_factor=float("inf"),
        max_drawdown=0.0,
        num_trades=1,
        equity_curve=[10_000.0, 10_005.0],
        strategy_name="EMA(9,21)",
        symbol="BTC",
        candles_tested=100,
    )


def test_save_backtest_result_typed_wrapper(db):
    result = make_backtest_result()
    analyser = PerformanceAnalyser()
    report = analyser.analyse(result)

    result_id = db.save_backtest_result("EMA(9,21)", "BTC", result, report)
    stored = db.get_analysis_result(result_id)

    assert stored["result_type"] == "backtest"
    assert stored["strategy_name"] == "EMA(9,21)"
    assert stored["summary"]["result"]["total_pnl"] == 5.0
    assert stored["summary"]["report"]["num_trades"] == 1
    # Nested Trade dataclass inside BacktestResult.trades must also
    # have been recursively serialized to a plain dict, not left as
    # an unparseable object reference.
    assert stored["summary"]["result"]["trades"][0]["pnl"] == 5.0


def test_save_out_of_sample_result_typed_wrapper(db):
    result = make_backtest_result()
    analyser = PerformanceAnalyser()
    report = analyser.analyse(result)
    split = OutOfSampleSplit(
        in_sample=[[1000, 1, 1, 1, 1, 1]],
        out_of_sample=[[2000, 1, 1, 1, 1, 1]],
        split_index=1,
        split_timestamp=2000,
    )
    oos_report = OutOfSampleReport(
        strategy_name="EMA(9,21)",
        split=split,
        in_sample_result=result,
        in_sample_report=report,
        out_of_sample_result=result,
        out_of_sample_report=report,
    )

    result_id = db.save_out_of_sample_result("BTC", oos_report)
    stored = db.get_analysis_result(result_id)

    assert stored["result_type"] == "out_of_sample"
    assert stored["strategy_name"] == "EMA(9,21)"
    assert stored["summary"]["split"]["split_index"] == 1


def test_save_walk_forward_result_typed_wrapper(db):
    result = make_backtest_result()
    analyser = PerformanceAnalyser()
    report = analyser.analyse(result)
    window = WalkForwardWindow(
        window_index=0,
        train=[[1000, 1, 1, 1, 1, 1]],
        test=[[2000, 1, 1, 1, 1, 1]],
    )
    window_result = WalkForwardWindowResult(
        window=window,
        strategy_name="EMA(9,21)",
        train_result=result,
        train_report=report,
        test_result=result,
        test_report=report,
    )
    wf_report = WalkForwardReport(window_results=[window_result])

    result_id = db.save_walk_forward_result("EMA(9,21)", "BTC", wf_report)
    stored = db.get_analysis_result(result_id)

    assert stored["result_type"] == "walk_forward"
    assert len(stored["summary"]["window_results"]) == 1
    assert stored["summary"]["window_results"][0]["strategy_name"] == "EMA(9,21)"


def test_save_monte_carlo_result_typed_wrapper(db):
    sim = MonteCarloSimulationResult(
        equity_curve=[10_000.0, 10_050.0],
        final_equity=10_050.0,
        total_pnl=50.0,
        max_drawdown=0.0,
    )
    mc_report = MonteCarloReport(
        num_simulations=1,
        method="shuffle",
        initial_capital=10_000.0,
        original_final_equity=10_050.0,
        original_max_drawdown=0.0,
        simulations=[sim],
    )

    result_id = db.save_monte_carlo_result("EMA(9,21)", "BTC", mc_report)
    stored = db.get_analysis_result(result_id)

    assert stored["result_type"] == "monte_carlo"
    assert stored["summary"]["num_simulations"] == 1
    assert stored["summary"]["simulations"][0]["final_equity"] == 10_050.0


# ──────────────────────────────────────────────────────────────
# PERSISTENCE ACROSS RESTARTS — the core requirement
# ──────────────────────────────────────────────────────────────


def test_data_survives_close_and_reopen(db_path):
    """
    THE regression test for this whole feature: data written in one
    Database instance (simulating one process run) must still be
    readable after that instance is closed and a brand-new Database
    instance opens the same file (simulating a restart).
    """
    db1 = Database(db_path=db_path)
    db1.save_session_start(
        session_id="s1",
        symbol="BTC",
        strategy="EMA",
        mode="paper",
        starting_balance=10_000.0,
    )
    db1.save_fill(session_id="s1", symbol="BTC", side="BUY", price=50_000.0, size=0.001)
    db1.save_trade(
        session_id="s1",
        symbol="BTC",
        side="LONG",
        entry_price=50_000.0,
        exit_price=51_000.0,
        size=0.001,
        pnl=1.0,
        pnl_pct=0.02,
    )
    result_id = db1.save_analysis_result("backtest", "EMA", "BTC", {"sharpe": 1.2})
    db1.close()

    db2 = Database(db_path=db_path)  # fresh instance, same file — simulates restart
    assert db2.get_session("s1") is not None
    assert len(db2.list_fills(session_id="s1")) == 1
    assert len(db2.list_trades(session_id="s1")) == 1
    assert db2.get_analysis_result(result_id)["summary"]["sharpe"] == 1.2
    db2.close()


# ──────────────────────────────────────────────────────────────
# JSON SERIALIZATION CORRECTNESS
# ──────────────────────────────────────────────────────────────


def test_summary_json_column_is_valid_json(db):
    result_id = db.save_analysis_result(
        "backtest", "EMA", "BTC", {"a": 1, "b": [1, 2, 3]}
    )
    stored = db.get_analysis_result(result_id)
    # summary_json must itself be parseable independently of get_analysis_result's convenience parsing.
    parsed = json.loads(stored["summary_json"])
    assert parsed == {"a": 1, "b": [1, 2, 3]}


# ──────────────────────────────────────────────────────────────
# VERSION STAMP + PRAGMA APPENDIX (append-only; no existing
# test above was modified)
# ──────────────────────────────────────────────────────────────


def test_p6_fresh_db_stamps_current_version(db):
    from core.db_migrations import CURRENT_SCHEMA_VERSION

    assert db.get_schema_version() == CURRENT_SCHEMA_VERSION


def test_p6_version_survives_reopen(db_path):
    from core.db_migrations import CURRENT_SCHEMA_VERSION

    db1 = Database(db_path=db_path)
    db1.close()
    db2 = Database(db_path=db_path)
    assert db2.get_schema_version() == CURRENT_SCHEMA_VERSION
    db2.close()


def test_p6_journal_mode_is_delete_and_verified(db):
    journal = str(db._conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    assert journal == "delete"


def test_p6_foreign_keys_enforced(db):
    fk = int(db._conn.execute("PRAGMA foreign_keys").fetchone()[0])
    assert fk == 1


def test_p6_verify_pragmas_rejects_wrong_settings(db):
    """_verify_pragmas must raise when the effective settings drift.

    Sabotages one pragma at a time (asserting the sabotage took
    effect first, so the test can never pass vacuously), then proves
    the gate raises — the same gate Database.__init__ runs before any
    trading loop can proceed.
    """
    from core.db_migrations import DatabaseSchemaError

    db._conn.execute("PRAGMA foreign_keys=OFF")
    assert int(db._conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 0
    with pytest.raises(DatabaseSchemaError, match="foreign_keys"):
        db._verify_pragmas()
    db._conn.execute("PRAGMA foreign_keys=ON")
    db._verify_pragmas()  # gate passes again once settings are restored

    db._conn.execute("PRAGMA busy_timeout=0")
    assert int(db._conn.execute("PRAGMA busy_timeout").fetchone()[0]) == 0
    with pytest.raises(DatabaseSchemaError, match="busy_timeout"):
        db._verify_pragmas()


def test_p6_synchronous_is_full_and_verified(db):
    """Durability choice: synchronous=FULL, so a committed risk-state
    write (including a latched kill) survives process crash, OS crash,
    and power loss — not just process crash as NORMAL would give."""
    sync_row = db._conn.execute("PRAGMA synchronous").fetchone()[0]
    assert int(sync_row) == 2  # 2 == FULL


def test_p6_init_failure_closes_connection(db_path, monkeypatch):
    """A failed Database() open must not leak its SQLite connection.

    Forces _verify_pragmas to raise after connect() has succeeded,
    then proves the created connection was closed (a closed
    connection rejects further use) — and that the original error
    (not a cleanup artifact) is what propagates to the fail-closed
    caller.
    """
    import sqlite3

    import core.database as db_module
    from core.db_migrations import DatabaseSchemaError

    created: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        created.append(conn)
        return conn

    def fail_verify(self):
        raise DatabaseSchemaError("simulated pragma failure")

    monkeypatch.setattr(db_module.sqlite3, "connect", tracking_connect)
    monkeypatch.setattr(Database, "_verify_pragmas", fail_verify)
    with pytest.raises(DatabaseSchemaError, match="simulated pragma failure"):
        Database(db_path=db_path)
    assert len(created) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        created[0].execute("SELECT 1")


def test_p6_init_failure_close_error_does_not_mask_original(
    db_path, monkeypatch, caplog
):
    """If cleanup itself errors, the original init failure still
    propagates — close problems are logged, never substituted."""
    import sqlite3

    import core.database as db_module
    from core.db_migrations import DatabaseSchemaError

    class _CloseFailingConn:
        """Forwards everything except close(), which raises."""

        def __init__(self, real: sqlite3.Connection) -> None:
            object.__setattr__(self, "_real", real)

        def __getattr__(self, name: str):
            return getattr(object.__getattribute__(self, "_real"), name)

        def __setattr__(self, name: str, value) -> None:
            setattr(object.__getattribute__(self, "_real"), name, value)

        def __enter__(self):
            object.__getattribute__(self, "_real").__enter__()
            return self

        def __exit__(self, *exc_info):
            return object.__getattribute__(self, "_real").__exit__(*exc_info)

        def close(self) -> None:
            raise OSError("simulated close failure")

    real_connect = sqlite3.connect

    def failing_close_connect(*args, **kwargs):
        return _CloseFailingConn(real_connect(*args, **kwargs))

    def fail_verify(self):
        raise DatabaseSchemaError("original failure")

    monkeypatch.setattr(db_module.sqlite3, "connect", failing_close_connect)
    monkeypatch.setattr(Database, "_verify_pragmas", fail_verify)
    with caplog.at_level("WARNING", logger="core.database"):
        with pytest.raises(DatabaseSchemaError, match="original failure"):
            Database(db_path=db_path)
    assert "simulated close failure" in caplog.text
