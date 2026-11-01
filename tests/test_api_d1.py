"""D1: FastAPI read-only foundation (health, sessions, overview).

TestClient against create_app(db_path=tmp) — temporary seeded
databases only, never the live database. No network, no exchange,
no credentials.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip(
    "fastapi",
    reason="API tests require the optional api group (poetry install --with api).",
)

from fastapi.testclient import TestClient  # noqa: E402  (after importorskip guard)

from api.main import create_app  # noqa: E402
from core.database import Database  # noqa: E402
from core.db_migrations import CURRENT_SCHEMA_VERSION  # noqa: E402


def seed_db(path: str) -> None:
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
            exit_price=51_000.0,
            size=0.001,
            pnl=1.0,
            pnl_pct=0.02,
        )
        db.save_session_end(
            session_id="s1", ending_balance=10_001.0, total_pnl=1.0, num_trades=1
        )
        db.save_session_start(
            session_id="s2",
            symbol="ETH",
            strategy="RSI",
            mode="paper",
            starting_balance=5_000.0,
        )
    finally:
        db.close()


def make_client(tmp_path) -> TestClient:
    path = str(tmp_path / "api.db")
    seed_db(path)
    return TestClient(create_app(db_path=path))


# ── Health ──


def test_health_ok_reports_schema_version(tmp_path) -> None:
    client = make_client(tmp_path)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "schema_version": CURRENT_SCHEMA_VERSION}


def test_health_empty_db_ok(tmp_path) -> None:
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_corrupt_db_is_503_not_healthy(tmp_path) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"\x00\x01not a sqlite database\xff\xfe")
    client = TestClient(create_app(db_path=str(bad)))
    resp = client.get("/api/health")
    assert resp.status_code == 503
    assert "detail" in resp.json()


def test_health_future_schema_is_503(tmp_path) -> None:
    import sqlite3

    path = str(tmp_path / "future.db")
    seed_db(path)
    con = sqlite3.connect(path)
    con.execute("UPDATE schema_version SET version = 999 WHERE id = 1")
    con.commit()
    con.close()
    client = TestClient(create_app(db_path=path))
    resp = client.get("/api/health")
    assert resp.status_code == 503


# ── Sessions ──


def test_list_sessions_returns_seeded(tmp_path) -> None:
    client = make_client(tmp_path)
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["session_id"] == "s2"  # newest first
    assert body[1]["session_id"] == "s1"
    assert body[1]["total_pnl"] == 1.0
    assert body[1]["ended_at"] is not None


def test_list_sessions_limit(tmp_path) -> None:
    client = make_client(tmp_path)
    resp = client.get("/api/sessions", params={"limit": 1})
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_get_session_by_id(tmp_path) -> None:
    client = make_client(tmp_path)
    resp = client.get("/api/sessions/s1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "BTC"
    assert body["num_trades"] == 1


def test_get_session_missing_is_404(tmp_path) -> None:
    client = make_client(tmp_path)
    resp = client.get("/api/sessions/does-not-exist")
    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_sessions_empty_db(tmp_path) -> None:
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    assert client.get("/api/sessions").json() == []


# ── Overview ──


def test_overview_shape_and_values(tmp_path) -> None:
    client = make_client(tmp_path)
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_trades"] == 1
    assert body["num_winning"] == 1
    assert body["num_losing"] == 0
    assert body["win_rate"] == 1.0
    assert body["total_pnl"] == 1.0
    assert body["avg_trade_pnl"] == 1.0
    assert len(body["recent_trades"]) == 1
    assert body["recent_trades"][0]["pnl"] == 1.0
    assert body["current_session"]["session_id"] == "s2"


def test_overview_empty_db_zeroed(tmp_path) -> None:
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    resp = client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_trades"] == 0
    assert body["recent_trades"] == []
    assert body["current_session"] is None


def test_overview_scoped_to_session(tmp_path) -> None:
    client = make_client(tmp_path)
    resp = client.get("/api/overview", params={"session_id": "s2"})
    assert resp.status_code == 200
    assert resp.json()["num_trades"] == 0  # s2 has no trades


def test_no_post_routes_exist(tmp_path) -> None:
    """D1 is strictly read-only: POST/PUT/PATCH/DELETE are not routed."""
    client = make_client(tmp_path)
    for method in ("post", "put", "patch", "delete"):
        resp = getattr(client, method)("/api/sessions")
        assert resp.status_code == 405
