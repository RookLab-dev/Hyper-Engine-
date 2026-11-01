"""backup and recovery against seeded database copies.

Uses the online backup API only — never a raw file copy of a live
database. Recovery means: backup → open the copy → verify rows, kill
state, schema version, and pragmas. All fixtures in tmp dirs.
"""

from __future__ import annotations

import pytest

from core.database import Database


def seed_db(db: Database) -> None:
    db.save_session_start(
        session_id="s-seed",
        symbol="BTC",
        strategy="EMA",
        mode="paper",
        starting_balance=10_000.0,
    )
    db.save_fill(
        session_id="s-seed", symbol="BTC", side="BUY", price=50_000.0, size=0.001
    )
    db.save_trade(
        session_id="s-seed",
        symbol="BTC",
        side="LONG",
        entry_price=50_000.0,
        exit_price=51_000.0,
        size=0.001,
        pnl=1.0,
        pnl_pct=0.02,
    )
    db.save_risk_state(
        symbol="BTC",
        mode="paper_trade",
        day="2026-10-23",
        session_id="s-seed",
        realized_loss=42.0,
        open_qty=0.25,
        entry_price=61_000.0,
        entry_fee=0.03,
        basis_known=True,
        total_pnl=-12.5,
        num_trades=3,
        kill_engaged=True,
        kill_reason="SEED_KILL",
    )


def assert_seed_intact(db: Database) -> None:
    assert db.get_session("s-seed") is not None
    assert len(db.list_fills(session_id="s-seed")) == 1
    trades = db.list_trades(session_id="s-seed")
    assert len(trades) == 1 and trades[0]["pnl"] == 1.0
    row = db.load_risk_state("BTC", "paper_trade", "2026-10-23")
    assert row is not None
    assert row["kill_engaged"] == 1
    assert row["kill_reason"] == "SEED_KILL"
    assert row["realized_loss"] == 42.0
    assert row["open_qty"] == 0.25
    assert row["entry_price"] == 61_000.0
    assert row["total_pnl"] == -12.5
    assert row["num_trades"] == 3


def test_backup_round_trip_preserves_everything(tmp_path) -> None:
    src = str(tmp_path / "src.db")
    dst = str(tmp_path / "nested" / "backup.db")
    db = Database(db_path=src)
    seed_db(db)
    db.backup_to(dst)  # must create missing parent dirs
    db.close()
    recovered = Database(db_path=dst)
    try:
        from core.db_migrations import CURRENT_SCHEMA_VERSION

        assert recovered.get_schema_version() == CURRENT_SCHEMA_VERSION
        assert_seed_intact(recovered)
    finally:
        recovered.close()


def test_backup_does_not_disturb_source(tmp_path) -> None:
    src = str(tmp_path / "src.db")
    dst = str(tmp_path / "backup.db")
    db = Database(db_path=src)
    seed_db(db)
    db.backup_to(dst)
    try:
        assert_seed_intact(db)  # source still fully usable after backup
    finally:
        db.close()


def test_garbage_file_fails_closed(tmp_path) -> None:
    """A non-database file must refuse to open, never silently half-load.

    (A merely incomplete copy with valid SQLite structure is repaired
    additively by IF NOT EXISTS DDL — that is the designed migration
    path, covered in test_db_migrations.py — so the fail-closed case
    tested here is an unreadable/corrupt file.)
    """
    dst = str(tmp_path / "torn.db")
    with open(dst, "wb") as f:
        f.write(b"\x00\x01\x02not a sqlite database\xff\xfe")
    with pytest.raises(Exception):
        Database(db_path=dst)


def test_backup_to_self_is_rejected_and_source_survives(tmp_path) -> None:
    """backup_to(source) must fail clearly before touching anything —
    and the source must stay fully usable afterwards."""
    from core.db_migrations import DatabaseSchemaError

    src = str(tmp_path / "src.db")
    db = Database(db_path=src)
    seed_db(db)
    with pytest.raises(DatabaseSchemaError, match="onto itself"):
        db.backup_to(src)
    try:
        assert_seed_intact(db)  # rejected backup disturbed nothing
    finally:
        db.close()


def test_restored_copy_enforces_pragmas(tmp_path) -> None:
    dst = str(tmp_path / "restored.db")
    db = Database(db_path=str(tmp_path / "src.db"))
    seed_db(db)
    db.backup_to(dst)
    db.close()
    recovered = Database(db_path=dst)
    try:
        journal = str(
            recovered._conn.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        assert journal == "delete"
        fk = int(recovered._conn.execute("PRAGMA foreign_keys").fetchone()[0])
        assert fk == 1
    finally:
        recovered.close()
