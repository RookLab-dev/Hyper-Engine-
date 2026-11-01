"""Finding 4 tests: smoke-test kill guard.

Covers kill_block_reason() (the pre-submission check in
execution/smoke_test.py::main) against both kill representations and
unverifiable state. main() itself is not executed here — it requires
credentials and network — so these tests pin the decision logic that
gates it, using temporary databases and kill files only.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.database import Database
from core.risk_state import utc_today
from execution.smoke_test import kill_block_reason


def test_clear_state_proceeds(tmp_path):
    db = Database(db_path=str(tmp_path / "smoke_guard.db"))
    try:
        assert (
            kill_block_reason("BTC", utc_today(), db, kill_path=str(tmp_path / "KILL"))
            is None
        )
    finally:
        db.close()


def test_kill_file_blocks(tmp_path):
    db = Database(db_path=str(tmp_path / "smoke_guard.db"))
    try:
        kill = tmp_path / "KILL"
        kill.write_text('{"reason": "OPERATOR"}', encoding="utf-8")
        reason = kill_block_reason("BTC", utc_today(), db, kill_path=str(kill))
        assert reason is not None and reason.startswith("KILL_FILE")
    finally:
        db.close()


def test_unreadable_kill_path_blocks(tmp_path):
    db = Database(db_path=str(tmp_path / "smoke_guard.db"))
    try:
        # A directory can never be read as the kill file: OSError on
        # every platform, deterministically unverifiable.
        reason = kill_block_reason("BTC", utc_today(), db, kill_path=str(tmp_path))
        assert reason == "UNVERIFIABLE"
    finally:
        db.close()


def test_db_flag_blocks_per_mode(tmp_path):
    db = Database(db_path=str(tmp_path / "smoke_guard.db"))
    try:
        db.save_risk_state(
            symbol="BTC",
            mode="paper_trade",
            day=utc_today(),
            session_id="s-prior",
            kill_engaged=True,
            kill_reason="LOOP_LATCH",
        )
        reason = kill_block_reason(
            "BTC", utc_today(), db, kill_path=str(tmp_path / "K")
        )
        assert reason is not None and "paper_trade" in reason
    finally:
        db.close()


def test_other_symbol_flag_does_not_block(tmp_path):
    db = Database(db_path=str(tmp_path / "smoke_guard.db"))
    try:
        db.save_risk_state(
            symbol="ETH",
            mode="paper_trade",
            day=utc_today(),
            session_id="s-prior",
            kill_engaged=True,
            kill_reason="LOOP_LATCH",
        )
        assert (
            kill_block_reason("BTC", utc_today(), db, kill_path=str(tmp_path / "K"))
            is None
        )
    finally:
        db.close()


def test_broken_db_blocks(tmp_path):
    broken = MagicMock()
    broken.load_risk_state.side_effect = OSError("disk gone")
    reason = kill_block_reason(
        "BTC", utc_today(), broken, kill_path=str(tmp_path / "K")
    )
    assert reason == "UNVERIFIABLE"


def test_prior_day_engaged_flag_blocks_after_rollover(tmp_path):
    """A kill latched yesterday must still block today when no loop has
    run since midnight (no today row exists to carry it forward)."""
    db = Database(db_path=str(tmp_path / "smoke_guard.db"))
    try:
        db.save_risk_state(
            symbol="BTC",
            mode="paper_trade",
            day="2000-01-02",
            session_id="s-prior-day",
            kill_engaged=True,
            kill_reason="LOOP_LATCH",
        )
        reason = kill_block_reason(
            "BTC", "2000-01-03", db, kill_path=str(tmp_path / "K")
        )
        assert reason is not None and "paper_trade" in reason
    finally:
        db.close()


def test_prior_day_clear_flag_does_not_block(tmp_path):
    """A non-engaged prior-day row must not block: only engaged flags
    carry forward as blocks."""
    db = Database(db_path=str(tmp_path / "smoke_guard.db"))
    try:
        db.save_risk_state(
            symbol="BTC",
            mode="paper_trade",
            day="2000-01-02",
            session_id="s-prior-day",
        )
        assert (
            kill_block_reason("BTC", "2000-01-03", db, kill_path=str(tmp_path / "K"))
            is None
        )
    finally:
        db.close()
