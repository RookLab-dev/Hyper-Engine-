"""tests: persistent risk state and kill switch.

Unit level pins RiskGate decisions (startup, per-tick gate, failure
counting/latching, reconciliation, reset). Loop level drives the real
paper_trade()/live_testnet_trade() loops with mocked market/executor
and asserts on the actual order-submission boundary (persisted orders
for paper, executor.submit_market_order for live) — kill-engaged,
unreadable, missing, mismatched, or unknown-basis runs must submit
nothing, while clear runs must submit (proving the tests are not
vacuous). No exchange, no network, no real database touched (tmp only).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import strategy_runner
from core.config import settings
from core.database import Database
from core.risk_state import (
    PERSIST_FAILURE_LATCH_THRESHOLD,
    R_COST_BASIS_UNKNOWN,
    R_KILL_FILE,
    R_KILL_FLAG,
    R_POSITION_MISMATCH,
    R_POSITION_UNTRACKED,
    R_STATE_CORRUPT,
    R_STATE_MISSING_UNEXPECTED,
    R_UNATTRIBUTED_CLOSURE,
    R_UNVERIFIABLE,
    RiskGate,
    read_kill_file,
    remove_kill_file,
    utc_today,
    write_kill_file,
)
from execution.testnet_executor import FillResult
from strategies.base_strategy import BaseStrategy
from strategy_runner import live_testnet_trade, paper_trade


# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmpdb(tmp_path):
    db = Database(db_path=str(tmp_path / "risk.db"))
    yield db
    db.close()


@pytest.fixture
def killfile(tmp_path):
    return str(tmp_path / "KILL_SWITCH")


def make_gate(db, killfile, mode="paper_trade", session_id="s-test"):
    return RiskGate(
        db=db,
        kill_path=killfile,
        symbol="BTC",
        mode=mode,
        session_id=session_id,
    )


def seed_row(db, *, kill=False, reason=None, **overrides):
    params = dict(
        symbol="BTC",
        mode="paper_trade",
        day=utc_today(),
        session_id="s-seed",
        realized_loss=10.0,
        open_qty=0.0,
        entry_price=0.0,
        entry_fee=0.0,
        basis_known=False,
        total_pnl=25.0,
        num_trades=2,
        kill_engaged=kill,
        kill_reason=reason,
    )
    params.update(overrides)
    db.save_risk_state(**params)


# ──────────────────────────────────────────────────────────────
# UNIT: startup
# ──────────────────────────────────────────────────────────────


def test_startup_first_run_writes_baseline(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    row = tmpdb.load_risk_state("BTC", "paper_trade", utc_today())
    assert row is not None
    assert row["realized_loss"] == 0.0
    assert row["kill_engaged"] == 0


def test_startup_resumes_counters(tmpdb, killfile):
    seed_row(tmpdb)
    gate = make_gate(tmpdb, killfile, session_id="s-new")
    assert gate.startup() is None
    assert gate.state.realized_loss == 10.0
    assert gate.state.total_pnl == 25.0
    assert gate.state.num_trades == 2


def test_startup_kill_file_blocks(tmpdb, killfile):
    write_kill_file(killfile, "OPERATOR_TEST")
    assert make_gate(tmpdb, killfile).startup() == R_KILL_FILE


def test_startup_db_kill_flag_blocks(tmpdb, killfile):
    seed_row(tmpdb, kill=True, reason="PRIOR_ENGAGEMENT")
    assert make_gate(tmpdb, killfile, session_id="s-new").startup() == R_KILL_FLAG


def test_startup_unexpected_missing_state_blocks(tmpdb, killfile):
    tmpdb.save_session_start(
        session_id="s-earlier-today",
        symbol="BTC",
        strategy="X",
        mode="paper_trade",
        starting_balance=10_000.0,
    )
    gate = make_gate(tmpdb, killfile, session_id="s-now")
    assert gate.startup() == R_STATE_MISSING_UNEXPECTED


def test_startup_corrupt_row_blocks(tmpdb, killfile):
    seed_row(tmpdb, realized_loss=-5.0)
    assert make_gate(tmpdb, killfile, session_id="s-new").startup() == R_STATE_CORRUPT


def test_startup_unreadable_db_blocks(killfile):
    broken = MagicMock()
    broken.load_risk_state.side_effect = sqlite3.OperationalError("disk gone")
    assert make_gate(broken, killfile).startup() == R_UNVERIFIABLE


# ──────────────────────────────────────────────────────────────
# UNIT: per-tick gate, failure counting, reset
# ──────────────────────────────────────────────────────────────


def test_check_passes_when_clear(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    assert gate.check() is None


def test_kill_file_appearing_mid_run_blocks(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    write_kill_file(killfile, "MID_RUN")
    assert gate.check() == R_KILL_FILE
    assert gate.check() == R_KILL_FILE  # latched


def test_unreadable_kill_check_blocks(monkeypatch, tmpdb, killfile):
    import core.risk_state as rs

    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    monkeypatch.setattr(rs, "read_kill_file", MagicMock(side_effect=OSError("EACCES")))
    assert gate.check() == R_UNVERIFIABLE


def test_startup_persist_failure_engages_immediately(tmpdb, killfile):
    """No baseline can be established, so startup fails closed at once —
    the 3-strike rule governs mid-run degradation, not a baseless start."""
    broken = MagicMock()
    broken.load_risk_state.return_value = None
    broken.list_sessions.return_value = []
    broken.load_latest_risk_state_before.return_value = None
    broken.save_risk_state.side_effect = OSError("disk gone")
    gate = make_gate(broken, killfile)
    assert gate.startup() == "PERSISTENCE_FAILED"


def test_three_strikes_latch_persistent_kill(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    broken = MagicMock()
    broken.save_risk_state.side_effect = OSError("disk gone")
    gate._db = broken  # inject mid-run persistence outage
    assert gate.persist() is False
    assert gate.consecutive_failures == 1
    assert not Path(
        killfile
    ).exists()  # not latched yet: single failure blocks, nothing more
    assert gate.persist() is False
    assert gate.consecutive_failures == 2
    assert not Path(killfile).exists()
    assert gate.persist() is False  # third consecutive failure latches
    assert gate.consecutive_failures >= PERSIST_FAILURE_LATCH_THRESHOLD
    assert Path(killfile).exists()  # latched to the file representation
    assert gate.check() == "PERSISTENCE_FAILED"


def test_single_failure_blocks_tick_but_does_not_latch(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    gate._failures = 1  # one transient failure, below threshold
    assert gate.check() == R_UNVERIFIABLE
    assert not Path(killfile).exists()


def test_clear_requires_and_reports_both_representations(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    gate.engage("UNIT_TEST")
    assert Path(killfile).exists()
    report = gate.clear()
    assert report == {"file_removed": True, "db_cleared": True}
    assert not Path(killfile).exists()
    assert gate.check() is None


def test_rebaseline_recovers_from_corrupt(tmpdb, killfile):
    seed_row(tmpdb, realized_loss=-5.0)
    gate = make_gate(tmpdb, killfile, session_id="s-new")
    assert gate.startup() == R_STATE_CORRUPT
    gate.rebaseline(realized_loss=1.0)
    fresh = make_gate(tmpdb, killfile, session_id="s-newer")
    assert fresh.startup() is None
    assert fresh.state.realized_loss == 1.0


def test_date_rollover_resets_loss(tmpdb, killfile):
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="paper_trade",
        session_id="s-old",
        day="2000-01-01",
    )
    assert gate.startup() is None
    gate.state.realized_loss = 5.0
    assert gate.check() is None
    assert gate.state.day == utc_today()
    assert gate.state.realized_loss == 0.0


def test_kill_file_helpers(tmp_path):
    p = tmp_path / "K"
    assert read_kill_file(p) is None
    assert remove_kill_file(p) is False
    write_kill_file(p, "WHY")
    assert read_kill_file(p) == "WHY"
    p.write_text("not json {{{", encoding="utf-8")
    assert read_kill_file(p) == "not json {{{"
    p.write_text("   ", encoding="utf-8")
    assert read_kill_file(p) == "UNKNOWN"
    assert remove_kill_file(p) is True
    assert read_kill_file(p) is None


# ──────────────────────────────────────────────────────────────
# UNIT: exchange reconciliation matrix (live)
# ──────────────────────────────────────────────────────────────


def _live_gate(tmpdb, killfile, **state_fields):
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="testnet_live_execution",
        session_id="s-live",
    )
    assert gate.startup() is None
    for key, value in state_fields.items():
        setattr(gate.state, key, value)
    return gate


def test_reconcile_flat_flat_is_clean(tmpdb, killfile):
    assert _live_gate(tmpdb, killfile).reconcile_exchange(0.0) is None


def test_reconcile_untracked_exchange_position_blocks(tmpdb, killfile):
    gate = _live_gate(tmpdb, killfile)
    assert gate.reconcile_exchange(0.05) == R_POSITION_UNTRACKED


def test_reconcile_unattributed_closure_blocks(tmpdb, killfile):
    gate = _live_gate(
        tmpdb, killfile, open_qty=0.05, entry_price=100.0, basis_known=True
    )
    assert gate.reconcile_exchange(0.0) == R_UNATTRIBUTED_CLOSURE


def test_reconcile_qty_mismatch_blocks(tmpdb, killfile):
    gate = _live_gate(
        tmpdb, killfile, open_qty=0.05, entry_price=100.0, basis_known=True
    )
    assert gate.reconcile_exchange(0.03) == R_POSITION_MISMATCH


def test_reconcile_unknown_basis_blocks(tmpdb, killfile):
    gate = _live_gate(
        tmpdb, killfile, open_qty=0.05, entry_price=0.0, basis_known=False
    )
    assert gate.reconcile_exchange(0.05) == R_COST_BASIS_UNKNOWN


def test_reconcile_matching_known_basis_passes(tmpdb, killfile):
    gate = _live_gate(
        tmpdb,
        killfile,
        open_qty=0.05,
        entry_price=100.0,
        entry_fee=0.01,
        basis_known=True,
    )
    assert gate.reconcile_exchange(0.05) is None


def test_reconcile_non_finite_exchange_blocks(tmpdb, killfile):
    gate = _live_gate(tmpdb, killfile)
    assert gate.reconcile_exchange(float("nan")) == R_UNVERIFIABLE


# ──────────────────────────────────────────────────────────────
# LOOP LEVEL: mocked market, real loops, real boundary assertions
# ──────────────────────────────────────────────────────────────


def loop_candles(n=60, start=100.0):
    out = []
    for i in range(n):
        close = start + float(i)
        open_ = (start + float(i - 1)) if i > 0 else close
        out.append(
            [
                1_000_000_000_000 + i * 3_600_000,
                open_,
                round(close * 1.01, 2),
                round(close * 0.99, 2),
                close,
                10.0,
            ]
        )
    return out


def buy_always_strategy():
    mock = MagicMock(spec=BaseStrategy)
    mock.name = "LoopBuyMock"
    mock.generate_signal.return_value = "BUY"
    return mock


@pytest.fixture
def loop_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "sleep_seconds", 0)
    monkeypatch.setattr(settings, "run_hours", 0.0001)
    monkeypatch.setattr(settings, "log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "symbol", "BTC")
    monkeypatch.setattr(settings, "interval", "1h")
    monkeypatch.setattr(settings, "position_size", 0.001)
    monkeypatch.setattr(settings, "initial_capital", 10_000.0)
    return settings


@pytest.fixture
def loop_db(monkeypatch, tmp_path):
    holder = {}
    db_path = str(tmp_path / "loop.db")

    def factory(*args, **kwargs):
        if "db" not in holder:
            holder["db"] = Database(db_path=db_path)
        return holder["db"]

    monkeypatch.setattr(strategy_runner, "Database", factory)
    db = factory()
    yield db
    db.close()


@pytest.fixture
def loop_kill(tmp_path):
    return str(tmp_path / "KILL_SWITCH")


def make_ohlcv(candles):
    mock = MagicMock()
    mock.fetch.return_value = candles
    return mock


def test_paper_clear_run_submits_orders(loop_settings, loop_db, loop_kill):
    """Control: a clear run MUST submit (proves the block tests below
    are not vacuous)."""
    paper_trade(buy_always_strategy(), make_ohlcv(loop_candles()), kill_path=loop_kill)
    assert len(loop_db.list_orders()) >= 1


def test_paper_kill_file_blocks_orders(loop_settings, loop_db, loop_kill):
    Path(loop_kill).write_text(json.dumps({"reason": "OPERATOR"}), encoding="utf-8")
    paper_trade(buy_always_strategy(), make_ohlcv(loop_candles()), kill_path=loop_kill)
    assert loop_db.list_orders() == []
    assert loop_db.list_trades() == []


def test_paper_db_kill_flag_blocks_orders(loop_settings, loop_db, loop_kill):
    loop_db.save_risk_state(
        symbol="BTC",
        mode="paper_trade",
        day=utc_today(),
        session_id="s-prior",
        kill_engaged=True,
        kill_reason="PRIOR",
    )
    paper_trade(buy_always_strategy(), make_ohlcv(loop_candles()), kill_path=loop_kill)
    assert loop_db.list_orders() == []


def test_paper_unreadable_db_blocks_without_crash(
    loop_settings, loop_kill, monkeypatch
):
    def raising_factory(*args, **kwargs):
        raise sqlite3.OperationalError("disk gone")

    monkeypatch.setattr(strategy_runner, "Database", raising_factory)
    # Must return cleanly (fail closed), never raise, never trade.
    assert (
        paper_trade(
            buy_always_strategy(), make_ohlcv(loop_candles()), kill_path=loop_kill
        )
        is None
    )


def test_paper_unexpected_missing_state_blocks(loop_settings, loop_db, loop_kill):
    loop_db.save_session_start(
        session_id="s-earlier-today",
        symbol="BTC",
        strategy="X",
        mode="paper_trade",
        starting_balance=10_000.0,
    )
    paper_trade(buy_always_strategy(), make_ohlcv(loop_candles()), kill_path=loop_kill)
    assert loop_db.list_orders() == []


def make_executor(*, position=0.0, open_orders=False):
    ex = MagicMock()
    ex.get_position_size.return_value = position
    ex.has_open_orders.return_value = open_orders
    ex.submit_market_order.return_value = 777
    ex.wait_for_fill.return_value = FillResult(
        oid=777, side="BUY", status="filled", filled_size=0.001, avg_price=150.0
    )
    return ex


def test_live_clear_run_submits_orders(loop_settings, loop_db, loop_kill):
    """Control: a clear live run MUST submit through the executor."""
    ex = make_executor()
    live_testnet_trade(
        buy_always_strategy(), make_ohlcv(loop_candles()), ex, kill_path=loop_kill
    )
    assert ex.submit_market_order.called


def test_live_kill_file_blocks_submit(loop_settings, loop_db, loop_kill):
    Path(loop_kill).write_text(json.dumps({"reason": "OPERATOR"}), encoding="utf-8")
    ex = make_executor()
    live_testnet_trade(
        buy_always_strategy(), make_ohlcv(loop_candles()), ex, kill_path=loop_kill
    )
    assert not ex.submit_market_order.called


def test_live_position_mismatch_blocks_and_latches_file(
    loop_settings, loop_db, loop_kill
):
    ex = make_executor(position=0.05)
    live_testnet_trade(
        buy_always_strategy(), make_ohlcv(loop_candles()), ex, kill_path=loop_kill
    )
    assert not ex.submit_market_order.called
    assert Path(loop_kill).exists()  # mismatch latched persistently
    assert read_kill_file(loop_kill) == R_POSITION_UNTRACKED


def test_live_unknown_basis_blocks_submit(loop_settings, loop_db, loop_kill):
    loop_db.save_risk_state(
        symbol="BTC",
        mode="testnet_live_execution",
        day=utc_today(),
        session_id="s-prior",
        open_qty=0.05,
        entry_price=0.0,
        entry_fee=0.0,
        basis_known=False,
    )
    ex = make_executor(position=0.05)
    live_testnet_trade(
        buy_always_strategy(), make_ohlcv(loop_candles()), ex, kill_path=loop_kill
    )
    assert not ex.submit_market_order.called
    assert read_kill_file(loop_kill) == R_COST_BASIS_UNKNOWN


def test_live_unreadable_db_blocks_without_crash(loop_settings, loop_kill, monkeypatch):
    def raising_factory(*args, **kwargs):
        raise sqlite3.OperationalError("disk gone")

    monkeypatch.setattr(strategy_runner, "Database", raising_factory)
    ex = make_executor()
    assert (
        live_testnet_trade(
            buy_always_strategy(), make_ohlcv(loop_candles()), ex, kill_path=loop_kill
        )
        is None
    )
    assert not ex.submit_market_order.called


# ──────────────────────────────────────────────────────────────
# FINDING 1: prior-day carry-forward
# ──────────────────────────────────────────────────────────────


def seed_prior_day(db, *, day="2000-01-02", mode="testnet_live_execution", **overrides):
    params = dict(
        symbol="BTC",
        mode=mode,
        day=day,
        session_id="s-prior-day",
        realized_loss=7.0,
        open_qty=0.05,
        entry_price=100.0,
        entry_fee=0.01,
        basis_known=True,
        total_pnl=12.0,
        num_trades=3,
    )
    params.update(overrides)
    db.save_risk_state(**params)


def test_carry_forward_matching_position_resumes(tmpdb, killfile):
    seed_prior_day(tmpdb)
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="testnet_live_execution",
        session_id="s-today",
        day="2000-01-03",
    )
    assert gate.startup() is None
    # Intent and cost basis recovered; daily loss reset per UTC semantics;
    # session totals mirror a no-restart rollover.
    assert gate.state.open_qty == 0.05
    assert gate.state.entry_price == 100.0
    assert gate.state.entry_fee == 0.01
    assert gate.state.basis_known is True
    assert gate.state.realized_loss == 0.0
    assert gate.state.total_pnl == 12.0
    assert gate.state.num_trades == 3
    assert gate.reconcile_exchange(0.05) is None


def test_carry_forward_mismatch_blocks(tmpdb, killfile):
    seed_prior_day(tmpdb)
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="testnet_live_execution",
        session_id="s-today",
        day="2000-01-03",
    )
    assert gate.startup() is None
    assert gate.reconcile_exchange(0.03) == R_POSITION_MISMATCH


def test_carry_forward_unknown_basis_blocks(tmpdb, killfile):
    seed_prior_day(tmpdb, basis_known=False, entry_price=0.0)
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="testnet_live_execution",
        session_id="s-today",
        day="2000-01-03",
    )
    assert gate.startup() is None
    assert gate.reconcile_exchange(0.05) == R_COST_BASIS_UNKNOWN


def test_carry_forward_flat_stays_flat(tmpdb, killfile):
    seed_prior_day(
        tmpdb, open_qty=0.0, entry_price=0.0, entry_fee=0.0, basis_known=False
    )
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="testnet_live_execution",
        session_id="s-today",
        day="2000-01-03",
    )
    assert gate.startup() is None
    assert gate.state.open_qty == 0.0
    assert gate.reconcile_exchange(0.0) is None


def test_carry_forward_preserves_prior_kill(tmpdb, killfile):
    seed_prior_day(tmpdb, kill_engaged=True, kill_reason="PRIOR_DAY")
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="testnet_live_execution",
        session_id="s-today",
        day="2000-01-03",
    )
    assert gate.startup() == R_KILL_FLAG
    row = tmpdb.load_risk_state("BTC", "testnet_live_execution", "2000-01-03")
    assert row is not None and row["kill_engaged"] == 1


def test_carry_forward_corrupt_prior_blocks(tmpdb, killfile):
    seed_prior_day(tmpdb, realized_loss=-1.0)
    gate = RiskGate(
        db=tmpdb,
        kill_path=killfile,
        symbol="BTC",
        mode="testnet_live_execution",
        session_id="s-today",
        day="2000-01-03",
    )
    assert gate.startup() == R_STATE_CORRUPT


# ──────────────────────────────────────────────────────────────
# FINDING 2: self-healing latch
# ──────────────────────────────────────────────────────────────


def test_heal_rewrites_missing_file_representation(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    gate.engage("HEAL_TEST")
    assert Path(killfile).exists()
    Path(killfile).unlink()  # file representation lost out-of-band
    assert gate.check() == "HEAL_TEST"  # latch itself still blocks
    assert Path(killfile).exists()  # healed on the tick


def test_heal_rewrites_missing_db_flag(tmpdb, killfile):
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    gate.engage("HEAL_TEST")
    tmpdb.clear_risk_kill("BTC", "paper_trade", utc_today())
    assert gate.check() == "HEAL_TEST"
    row = tmpdb.load_risk_state("BTC", "paper_trade", utc_today())
    assert row is not None and row["kill_engaged"] == 1  # healed


def test_heal_repeated_failure_stays_latched_and_loud(tmpdb, tmp_path, caplog):
    killfile = str(tmp_path / "KILL_SWITCH")
    gate = make_gate(tmpdb, killfile)
    assert gate.startup() is None
    gate.engage("HEAL_TEST")  # file lands, DB flag lands, latch holds
    assert Path(killfile).exists()
    # Destroy the file representation out-of-band: replace it with a
    # directory, so every subsequent file op raises OSError on any
    # platform while the DB flag stays intact.
    Path(killfile).unlink()
    (tmp_path / "KILL_SWITCH").mkdir()
    for _ in range(3):
        assert gate.check() == "HEAL_TEST"
    assert "CRITICAL" in caplog.text


def test_restart_with_both_stores_dead_stays_blocked(tmp_path):
    dead_dir = tmp_path / "unwritable_dir"
    dead_dir.mkdir()
    broken = MagicMock()
    broken.load_risk_state.side_effect = sqlite3.OperationalError("disk gone")
    broken.list_sessions.return_value = []
    gate = RiskGate(
        db=broken,
        kill_path=str(dead_dir),
        symbol="BTC",
        mode="paper_trade",
        session_id="s-restart",
    )
    # File check fails first (directory unreadable as a file) → blocked
    # without ever trusting the equally dead database.
    assert gate.startup() == R_UNVERIFIABLE


# ──────────────────────────────────────────────────────────────
# FINDING 3: paper SELL boundary re-check
# ──────────────────────────────────────────────────────────────


def test_paper_mid_tick_kill_blocks_sell_fill(
    loop_settings, loop_db, loop_kill, monkeypatch
):
    """Engages the kill between the tick-top check and the paper SELL
    boundary: the SELL fill must not be recorded. Only the tick-1 BUY
    fill may exist (plus the session-end force-close of the stranded
    position, which is teardown accounting, not a new decision)."""
    import core.risk_state as rs

    real_read = rs.read_kill_file
    calls = {"n": 0}

    def counting_read(path):
        # startup(1), tick-1 top(2), tick-1 BUY boundary(3), tick-2 top(4)
        # pass; the tick-2 SELL boundary(5+) must see the kill.
        calls["n"] += 1
        if calls["n"] <= 4:
            return real_read(path)
        return "TEST_MID_TICK"

    monkeypatch.setattr(rs, "read_kill_file", counting_read)

    ticks = {"n": 0}

    def signal(closes):
        ticks["n"] += 1
        if ticks["n"] == 1:
            return "BUY"
        if ticks["n"] == 2:
            return "SELL"
        return "HOLD"

    strategy = MagicMock(spec=BaseStrategy)
    strategy.name = "MidTickMock"
    strategy.generate_signal.side_effect = signal

    paper_trade(strategy, make_ohlcv(loop_candles()), kill_path=loop_kill)

    assert calls["n"] >= 5  # the SELL boundary check was actually reached
    fills = loop_db.list_fills()
    assert [f["side"] for f in fills] == ["BUY"]  # no SELL fill recorded
    orders = loop_db.list_orders()
    assert [o["side"] for o in orders] == ["BUY"]  # no SELL order recorded


# ──────────────────────────────────────────────────────────────
# GRACEFUL SHUTDOWN (SIGTERM): stop flag, loop boundary, cleanup
# ──────────────────────────────────────────────────────────────
# Append-only: no existing test above was modified. All fixtures reuse
# the loop_settings/loop_db/loop_kill helpers (tmp DBs, mocked
# market/executor, zero network). Each test resets the module stop
# flag first (autouse) so ordering can never leak a request.


@pytest.fixture(autouse=True)
def _reset_stop_flag():
    strategy_runner.reset_shutdown_flag()
    yield
    strategy_runner.reset_shutdown_flag()


def test_shutdown_flag_defaults_clear() -> None:
    assert strategy_runner.shutdown_requested() is False


def test_request_shutdown_sets_flag() -> None:
    strategy_runner.request_shutdown()
    assert strategy_runner.shutdown_requested() is True


def test_sigterm_handler_requests_shutdown(monkeypatch) -> None:
    """install_shutdown_handlers() chains: the OS-level SIGTERM handler
    it installs must set the stop flag when invoked."""
    import signal as signal_module

    installed: dict = {}

    def fake_signal(sig, handler):
        installed[sig] = handler
        return signal_module.SIG_DFL

    monkeypatch.setattr(signal_module, "signal", fake_signal)
    monkeypatch.setattr(signal_module, "getsignal", lambda sig: signal_module.SIG_DFL)
    strategy_runner.install_shutdown_handlers()
    assert signal_module.SIGTERM in installed
    installed[signal_module.SIGTERM](signal_module.SIGTERM, None)
    assert strategy_runner.shutdown_requested() is True


def test_install_shutdown_handlers_chains_previous(monkeypatch) -> None:
    """A pre-existing SIGTERM handler still runs (then shutdown is
    requested) — installing ours never silently drops prior behavior."""
    import signal as signal_module

    prior_calls: list = []
    monkeypatch.setattr(
        signal_module, "getsignal", lambda sig: (lambda s, f: prior_calls.append(s))
    )
    captured: dict = {}
    monkeypatch.setattr(
        signal_module, "signal", lambda sig, h: captured.setdefault("h", h)
    )
    strategy_runner.install_shutdown_handlers()
    captured["h"](signal_module.SIGTERM, None)
    assert prior_calls == [signal_module.SIGTERM]
    assert strategy_runner.shutdown_requested() is True


def test_paper_shutdown_before_first_tick_trades_nothing(
    loop_settings, loop_db, loop_kill
):
    """A stop requested before the loop starts must not initiate any
    trading iteration — yet session-end state is still persisted."""
    strategy_runner.request_shutdown()
    paper_trade(buy_always_strategy(), make_ohlcv(loop_candles()), kill_path=loop_kill)
    assert loop_db.list_orders() == []
    assert loop_db.list_trades() == []
    sessions = loop_db.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["ended_at"] is not None


def test_paper_shutdown_mid_run_force_closes_and_ends_session(
    loop_settings, loop_db, loop_kill
):
    """Stop after the BUY tick: the loop exits via the existing finally
    path — simulated position force-closed, session ended, counters
    persisted. The stop must not start another iteration."""
    ticks = {"n": 0}

    def signal(closes):
        ticks["n"] += 1
        if ticks["n"] == 1:
            return "BUY"
        strategy_runner.request_shutdown()
        return "HOLD"

    strategy = MagicMock(spec=BaseStrategy)
    strategy.name = "ShutdownMock"
    strategy.generate_signal.side_effect = signal

    paper_trade(strategy, make_ohlcv(loop_candles()), kill_path=loop_kill)
    # BUY fill from tick 1, force-close trade from finally teardown.
    assert [o["side"] for o in loop_db.list_orders()] == ["BUY"]
    trades = loop_db.list_trades()
    assert len(trades) == 1
    sessions = loop_db.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["ended_at"] is not None
    assert sessions[0]["num_trades"] == 1
    # Persisted risk state: flat intent, totals carried.
    row = loop_db.load_risk_state("BTC", "paper_trade", utc_today())
    assert row is not None
    assert float(row["open_qty"]) == 0.0
    assert int(row["num_trades"]) == 1


def test_live_shutdown_before_first_tick_submits_nothing(
    loop_settings, loop_db, loop_kill
):
    """Live loop honors the same boundary: pre-requested stop submits
    no real orders, still ends the session cleanly."""
    strategy_runner.request_shutdown()
    live_testnet_trade(
        buy_always_strategy(),
        make_ohlcv(loop_candles()),
        make_executor(),
        kill_path=loop_kill,
    )
    sessions = loop_db.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["ended_at"] is not None


def test_live_shutdown_mid_run_leaves_position_for_manual_close(
    loop_settings, loop_db, loop_kill
):
    """Stop after a confirmed live BUY: the loop exits WITHOUT
    force-closing — live cleanup warns and leaves the real position
    for manual close (existing distinction preserved). Session ends,
    position intent persists for reconcile on restart."""
    ticks = {"n": 0}

    def signal(closes):
        ticks["n"] += 1
        if ticks["n"] == 1:
            return "BUY"
        strategy_runner.request_shutdown()
        return "HOLD"

    strategy = MagicMock(spec=BaseStrategy)
    strategy.name = "LiveShutdownMock"
    strategy.generate_signal.side_effect = signal

    # Exchange starts flat (clean reconcile) and opens only once the
    # mocked BUY is submitted — mirroring a real fill lifecycle.
    executor = make_executor(position=0.0)
    exchange = {"qty": 0.0}

    def submit_and_open(*args, **kwargs):
        exchange["qty"] = 0.001
        return 777

    executor.submit_market_order.side_effect = submit_and_open
    executor.get_position_size.side_effect = lambda: exchange["qty"]
    live_testnet_trade(
        strategy, make_ohlcv(loop_candles()), executor, kill_path=loop_kill
    )
    # No SELL submitted after the stop; only the tick-1 BUY exists.
    assert executor.submit_market_order.call_count == 1
    sessions = loop_db.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["ended_at"] is not None
    # Position intent persists (open_qty carried) for restart reconcile.
    row = loop_db.load_risk_state("BTC", "testnet_live_execution", utc_today())
    assert row is not None
    assert float(row["open_qty"]) == 0.001
