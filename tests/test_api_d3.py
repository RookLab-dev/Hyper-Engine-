"""D3: risk and runtime observability (GET-only).

TestClient against create_app(db_path=tmp) — temporary seeded
databases and temporary kill-file paths only, never the live
database or the project's real storage/KILL_SWITCH file. No
network, no exchange, no credentials. Validated in an environment
with the optional api group installed (poetry install --with api).
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip(
    "fastapi",
    reason="API tests require the optional api group (poetry install --with api).",
)

from fastapi.testclient import TestClient  # noqa: E402  (after importorskip guard)

from api.main import create_app  # noqa: E402
from core.database import Database  # noqa: E402
from core.risk_state import utc_today  # noqa: E402


def seed_db(path: str, *, kill: bool = False, kill_reason: str = "TEST") -> None:
    db = Database(db_path=path)
    try:
        db.save_session_start(
            session_id="s1",
            symbol="BTC",
            strategy="EMA",
            mode="paper",
            starting_balance=10_000.0,
        )
        db.save_trade(
            session_id="s1",
            symbol="BTC",
            side="LONG",
            entry_price=50_000.0,
            exit_price=49_000.0,
            size=0.001,
            pnl=-1.0,
            pnl_pct=-0.02,
        )
        db.save_risk_state(
            symbol="BTC",
            mode="paper_trade",
            day=utc_today(),
            session_id="s1",
            realized_loss=1.0,
            total_pnl=-1.0,
            num_trades=1,
            kill_engaged=kill,
            kill_reason=kill_reason if kill else None,
        )
    finally:
        db.close()


def _write_kill_file(path: str, reason: str) -> None:
    """Writes an isolated kill file at a tmp path (never the real one)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"reason": reason}, f)


@pytest.fixture
def kill_env(tmp_path, monkeypatch):
    """Points the kill-path env seam at a nonexistent tmp path.

    Tests that never touch the kill file use this so a real
    storage/KILL_SWITCH in the developer's checkout can never leak
    into (or be touched by) the suite. Tests exercising kill states
    build their own client via make_client(), which sets the seam
    itself. monkeypatch restores the previous value afterwards."""
    from api import deps

    monkeypatch.setenv(deps.KILL_PATH_ENV_VAR, str(tmp_path / "KILL_SWITCH_ABSENT"))
    return deps


def make_client(
    tmp_path,
    monkeypatch,
    *,
    kill: bool = False,
    kill_reason: str = "TEST",
    kill_file: str | None = None,
) -> TestClient:
    """Isolated app: tmp DB; kill file ONLY via explicit tmp paths.

    The real storage/KILL_SWITCH is never used: the env seam always
    points at a tmp path (nonexistent = clear). ``kill_file="WRITE"``
    creates an engaged file; any other string is used as the path
    directly (nonexistent paths read as clear, directories read as
    unverifiable). Env is managed with monkeypatch so it cannot leak
    between tests.
    """
    from api import deps

    path = str(tmp_path / "api_d3.db")
    seed_db(path, kill=kill, kill_reason=kill_reason)
    if kill_file is None:
        kill_file = str(tmp_path / "KILL_SWITCH_ABSENT")
    if kill_file == "WRITE":
        kill_file = str(tmp_path / "KILL_SWITCH")
        _write_kill_file(kill_file, "OPERATOR_FILE")
    monkeypatch.setenv(deps.KILL_PATH_ENV_VAR, kill_file)
    return TestClient(create_app(db_path=path))


# ── Risk overview ──


def test_risk_overview_shape(tmp_path, monkeypatch, kill_env) -> None:
    _ = kill_env
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/api/risk/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured_max_position_pct"] == pytest.approx(0.05)
    assert body["configured_max_open_positions"] >= 1
    # One losing trade today in the seed.
    assert body["today_realized_loss_usdc"] == pytest.approx(1.0)
    assert body["today_loss_pct_of_cap"] >= 0.0


def test_risk_overview_empty_db(tmp_path, kill_env) -> None:
    _ = kill_env
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    resp = client.get("/api/risk/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["today_realized_loss_usdc"] == 0.0
    assert body["today_loss_pct_of_cap"] == 0.0


# ── Kill status ──


def test_kill_status_clear(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/api/risk/kill-status", params={"symbol": "BTC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTC"
    assert body["blocked"] is False
    assert body["block_reason"] is None
    assert body["file"]["present"] is False


def test_kill_status_file_only(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch, kill_file="WRITE")
    resp = client.get("/api/risk/kill-status", params={"symbol": "BTC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    assert body["file"]["present"] is True
    assert "OPERATOR_FILE" in (body["block_reason"] or "")


def test_kill_status_db_only(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch, kill=True, kill_reason="DB_LATCH")
    resp = client.get("/api/risk/kill-status", params={"symbol": "BTC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    assert "DB_LATCH" in (body["block_reason"] or "")
    assert body["modes"]["paper_trade"]["kill_engaged"] is True


def test_kill_status_both(tmp_path, monkeypatch) -> None:
    client = make_client(
        tmp_path, monkeypatch, kill=True, kill_reason="DB_LATCH", kill_file="WRITE"
    )
    body = client.get("/api/risk/kill-status", params={"symbol": "BTC"}).json()
    assert body["blocked"] is True
    # File takes precedence in the authoritative decision.
    assert body["file"]["present"] is True


def test_kill_status_prior_day_kill_blocks(tmp_path, monkeypatch) -> None:
    """A kill latched yesterday (no row today) still blocks — the
    rollover-safe prior-day lookup. Uses an old UTC date row."""
    from datetime import datetime, timedelta, timezone

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    assert yesterday < utc_today()
    path = str(tmp_path / "rollover.db")
    db = Database(db_path=path)
    try:
        db.save_risk_state(
            symbol="BTC",
            mode="paper_trade",
            day=yesterday,
            session_id="s-old",
            kill_engaged=True,
            kill_reason="YESTERDAY_LATCH",
        )
    finally:
        db.close()
    from api import deps

    monkeypatch.setenv(deps.KILL_PATH_ENV_VAR, str(tmp_path / "KILL_ABSENT"))
    client = TestClient(create_app(db_path=path))
    body = client.get("/api/risk/kill-status", params={"symbol": "BTC"}).json()
    assert body["blocked"] is True
    assert "YESTERDAY_LATCH" in (body["block_reason"] or "")


def test_kill_status_unreadable_file_blocks(tmp_path, monkeypatch) -> None:
    """A kill file that cannot be read is unverifiable — blocked,
    never reported clear.

    Simulates OSError at the kill-file read boundary: patching
    Path.exists (as looked up on pathlib.Path, which read_kill_file
    calls as p.exists()) to raise forces both the diagnostics read
    and the authoritative kill_block_reason() down their real
    OSError → UNVERIFIABLE paths on every OS. Windows returns False
    (not an error) for exists() on paths under a regular file, so no
    filesystem layout can exercise this path reliably there.
    """
    import pathlib

    real_exists = pathlib.Path.exists

    def raising_exists(self: pathlib.Path) -> bool:
        # Match on the file NAME containing KILL_SWITCH (not endswith):
        # the isolated test seam points at a "KILL_SWITCH_ABSENT"
        # sentinel path, which endswith("KILL_SWITCH") misses — so the
        # simulated OSError never fired and the file read as clear.
        if "KILL_SWITCH" in self.name:
            raise OSError("simulated unreadable kill file")
        return real_exists(self)

    monkeypatch.setattr(pathlib.Path, "exists", raising_exists)
    client = make_client(tmp_path, monkeypatch)
    body = client.get("/api/risk/kill-status", params={"symbol": "BTC"}).json()
    assert body["blocked"] is True
    assert body["file"].get("unverifiable") is True
    assert "UNVERIFIABLE" in (body["block_reason"] or "")


def test_kill_status_unreadable_db_blocks(tmp_path, monkeypatch) -> None:
    """A database whose reads all fail is unverifiable — every mode
    reports unverifiable and the overall status blocks."""
    import core.database as db_module

    client = make_client(tmp_path, monkeypatch)

    def raising_load(self, *args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(db_module.Database, "load_risk_state", raising_load)
    monkeypatch.setattr(
        db_module.Database, "load_latest_risk_state_before", raising_load
    )
    body = client.get("/api/risk/kill-status", params={"symbol": "BTC"}).json()
    assert body["blocked"] is True
    assert body["modes"]["paper_trade"].get("unverifiable") is True


# ── Risk state row ──


def test_risk_state_present(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/api/risk/state", params={"symbol": "BTC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "paper_trade"
    assert body["corrupt"] is False
    assert body["kill_engaged"] is False
    assert body["total_pnl"] == -1.0


def test_risk_state_missing_is_404(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/api/risk/state", params={"symbol": "BTC", "mode": "smoke_test"})
    assert resp.status_code == 404


def test_risk_state_unknown_mode_is_422(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    resp = client.get("/api/risk/state", params={"mode": "bogus"})
    assert resp.status_code == 422


def test_risk_state_corrupt_row_flagged(tmp_path, monkeypatch) -> None:
    """A stored row failing validation is exposed raw with
    corrupt: true — never repaired, never coerced."""
    from api import deps

    path = str(tmp_path / "corrupt.db")
    seed_db(path)
    db = Database(db_path=path)
    try:
        db.save_risk_state(
            symbol="BTC",
            mode="paper_trade",
            day=utc_today(),
            session_id="s-bad",
            realized_loss=-5.0,  # negative realized_loss is corrupt
            kill_engaged=True,
            kill_reason="X",
        )
    finally:
        db.close()
    monkeypatch.setenv(deps.KILL_PATH_ENV_VAR, str(tmp_path / "K_ABSENT"))
    client = TestClient(create_app(db_path=path))
    resp = client.get("/api/risk/state", params={"symbol": "BTC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corrupt"] is True
    # Raw stored values preserved (negative loss NOT normalized).
    assert body["realized_loss"] == -5.0
    assert body["kill_engaged"] is True


def test_risk_state_non_finite_null(tmp_path) -> None:
    """Non-finite stored floats serialize as JSON null."""
    from api.schemas import RiskStateResponse

    resp = RiskStateResponse(
        symbol="BTC",
        mode="paper_trade",
        day="2026-01-01",
        session_id="s",
        realized_loss=float("inf"),
        open_qty=float("nan"),
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["realized_loss"] is None
    assert dumped["open_qty"] is None


# ── Safety: read-only + failure behavior ──


def test_d3_routes_reject_writes(tmp_path, monkeypatch) -> None:
    client = make_client(tmp_path, monkeypatch)
    for method in ("post", "put", "patch", "delete"):
        for path in ("/api/risk/overview", "/api/risk/kill-status", "/api/risk/state"):
            resp = getattr(client, method)(path)
            assert resp.status_code == 405


def test_d3_routes_503_on_corrupt_db(tmp_path, kill_env) -> None:
    _ = kill_env
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"\x00\x01not a sqlite database\xff\xfe")
    client = TestClient(create_app(db_path=str(bad)))
    for path in ("/api/risk/overview", "/api/risk/kill-status", "/api/risk/state"):
        assert client.get(path).status_code == 503


def test_kill_file_never_written(tmp_path, monkeypatch) -> None:
    """GET requests must not create, modify, or clear the kill file —
    an absent tmp kill path stays absent after every D3 call."""
    kill_file = str(tmp_path / "K_MUST_STAY_ABSENT")
    client = make_client(tmp_path, monkeypatch, kill_file=kill_file)
    for path in ("/api/risk/overview", "/api/risk/kill-status", "/api/risk/state"):
        client.get(path, params={"symbol": "BTC"})
    import pathlib

    assert not pathlib.Path(kill_file).exists()
