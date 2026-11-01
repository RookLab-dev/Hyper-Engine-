"""D2: history, performance, and analysis endpoints (GET-only).

TestClient against create_app(db_path=tmp) — temporary seeded
databases only, never the live database. No network, no exchange,
no credentials. Validated in an environment with the optional api
group installed (poetry install --with api).
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip(
    "fastapi",
    reason="API tests require the optional api group (poetry install --with api).",
)

from fastapi.testclient import TestClient  # noqa: E402  (after importorskip guard)

from api.main import create_app  # noqa: E402
from backtester.backtester import BacktestResult, Trade  # noqa: E402
from backtester.monte_carlo import (  # noqa: E402
    MonteCarloReport,
    MonteCarloSimulationResult,
)
from backtester.performance import PerformanceAnalyser  # noqa: E402
from core.database import Database  # noqa: E402


def seed_db(path: str) -> dict[str, int]:
    """Seeds sessions, one order/fill/trade, one backtest + one Monte
    Carlo result. Returns the persisted analysis row ids."""
    db = Database(db_path=path)
    try:
        db.save_session_start(
            session_id="s1",
            symbol="BTC",
            strategy="EMA",
            mode="paper",
            starting_balance=10_000.0,
        )
        db.save_order(
            session_id="s1",
            symbol="BTC",
            side="BUY",
            price=50_000.0,
            size=0.001,
            order_type="MARKET",
        )
        db.save_fill(
            session_id="s1",
            symbol="BTC",
            side="BUY",
            price=50_050.0,
            size=0.001,
            fee=0.05,
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
        trade = Trade(
            entry_time=1000,
            exit_time=2000,
            entry_price=100.0,
            exit_price=105.0,
            size=1.0,
            pnl=5.0,
            pnl_pct=0.05,
        )
        result = BacktestResult(
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
        report = PerformanceAnalyser().analyse(result)
        backtest_id = db.save_backtest_result("EMA(9,21)", "BTC", result, report)
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
        mc_id = db.save_monte_carlo_result("EMA(9,21)", "BTC", mc_report)
        return {"backtest_id": backtest_id, "mc_id": mc_id}
    finally:
        db.close()


def make_client(tmp_path) -> tuple[TestClient, dict[str, int]]:
    path = str(tmp_path / "api_d2.db")
    ids = seed_db(path)
    return TestClient(create_app(db_path=path)), ids


# ── Trades ──


def test_trades_lists_seeded_trade(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    resp = client.get("/api/trades")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pnl"] == 1.0
    assert body[0]["session_id"] == "s1"


def test_trades_filters_and_empty(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    assert client.get("/api/trades", params={"symbol": "ETH"}).json() == []
    assert client.get("/api/trades", params={"side": "SHORT"}).json() == []
    assert len(client.get("/api/trades", params={"side": "LONG"}).json()) == 1
    assert client.get("/api/trades", params={"session_id": "nope"}).json() == []


def test_trades_empty_db(tmp_path) -> None:
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    assert client.get("/api/trades").json() == []


# ── Orders & fills ──


def test_orders_lists_seeded_order(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    resp = client.get("/api/orders")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["side"] == "BUY"
    assert body[0]["order_type"] == "MARKET"


def test_fills_lists_seeded_fill(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    resp = client.get("/api/fills")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["fee"] == 0.05


def test_orders_fills_empty_db(tmp_path) -> None:
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    assert client.get("/api/orders").json() == []
    assert client.get("/api/fills").json() == []


# ── Performance ──


def test_live_performance_shape(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    resp = client.get("/api/performance/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_trades"] == 1
    assert body["total_pnl"] == 1.0
    assert body["win_rate"] == 1.0
    assert body["equity_curve"] == [0.0, 1.0]
    # Single all-winning trade: no losing trades means profit_factor
    # is +inf in the engine — the API must serialize it as JSON null,
    # not emit non-compliant Infinity (and not invent a finite number).
    assert body["profit_factor"] is None


def test_live_performance_empty_zeroed(tmp_path) -> None:
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    resp = client.get("/api/performance/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_trades"] == 0
    assert body["equity_curve"] == []
    assert body["profit_factor"] == 0.0


def test_live_performance_non_finite_all_null(tmp_path) -> None:
    """inf, -inf, and NaN in any performance field serialize as null.

    Unit-covers the schema-level sanitizer directly (the seeded DB
    only produces +inf profit_factor; sharpe_ratio is finite there).
    """
    from api.schemas import LivePerformanceResponse, sanitize_json_floats

    assert sanitize_json_floats(float("inf")) is None
    assert sanitize_json_floats(float("-inf")) is None
    assert sanitize_json_floats(float("nan")) is None
    assert sanitize_json_floats(1.5) == 1.5
    assert sanitize_json_floats("keep") == "keep"

    resp = LivePerformanceResponse(
        num_trades=2,
        total_pnl=0.0,
        win_rate=0.5,
        profit_factor=float("inf"),
        expectancy=0.0,
        sharpe_ratio=float("nan"),
        max_drawdown=0.0,
        equity_curve=[0.0, 1.0, float("-inf")],
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["profit_factor"] is None
    assert dumped["sharpe_ratio"] is None
    assert dumped["equity_curve"] == [0.0, 1.0, None]
    # Finite values pass through untouched.
    assert dumped["total_pnl"] == 0.0
    assert dumped["win_rate"] == 0.5


def test_backtest_performance_passthrough(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    resp = client.get("/api/performance/backtest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report"]["num_trades"] == 1
    assert body["report"]["total_pnl"] == 5.0
    assert body["result"]["strategy_name"] == "EMA(9,21)"
    # Seeded backtest is all-winning: stored report profit_factor is
    # +inf — must serialize as JSON null, not raise.
    assert body["report"]["profit_factor"] is None


def test_backtest_performance_nan_sanitized(tmp_path) -> None:
    """NaN nested in a stored report also serializes as null."""
    from api.schemas import BacktestPerformanceResponse

    resp = BacktestPerformanceResponse(
        result={"strategy_name": "X", "nested": {"v": float("nan")}},
        report={"profit_factor": float("-inf"), "ok": 1.0},
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["result"]["nested"]["v"] is None
    assert dumped["report"]["profit_factor"] is None
    assert dumped["report"]["ok"] == 1.0


def test_backtest_performance_missing_is_404(tmp_path) -> None:
    client = TestClient(create_app(db_path=str(tmp_path / "empty.db")))
    resp = client.get("/api/performance/backtest")
    assert resp.status_code == 404


# ── Analysis ──


def test_analysis_lists_results(tmp_path) -> None:
    client, ids = make_client(tmp_path)
    resp = client.get("/api/analysis")
    assert resp.status_code == 200
    body = resp.json()
    assert {r["id"] for r in body} == {ids["backtest_id"], ids["mc_id"]}
    assert {r["result_type"] for r in body} == {"backtest", "monte_carlo"}


def test_analysis_filters_by_type(tmp_path) -> None:
    client, ids = make_client(tmp_path)
    resp = client.get("/api/analysis", params={"result_type": "backtest"})
    assert resp.status_code == 200
    body = resp.json()
    assert [r["id"] for r in body] == [ids["backtest_id"]]


def test_analysis_detail_backtest(tmp_path) -> None:
    client, ids = make_client(tmp_path)
    resp = client.get(f"/api/analysis/{ids['backtest_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result_type"] == "backtest"
    assert body["summary"]["result"]["total_pnl"] == 5.0
    assert body["aggregates"] is None
    # Seeded backtest report carries +inf profit_factor — must come
    # back as JSON null through the detail endpoint too.
    assert body["summary"]["report"]["profit_factor"] is None


def test_analysis_detail_nan_sanitized(tmp_path) -> None:
    """NaN nested in a stored summary serializes as null."""
    from api.schemas import AnalysisDetailResponse

    resp = AnalysisDetailResponse(
        id=1,
        result_type="backtest",
        strategy_name="X",
        symbol="BTC",
        created_at="2026-01-01T00:00:00+00:00",
        summary={"deep": {"v": float("nan")}},
        aggregates={"m": float("-inf")},
    )
    dumped = resp.model_dump(mode="json")
    assert dumped["summary"]["deep"]["v"] is None
    assert dumped["aggregates"] == {"m": None}


def test_analysis_detail_missing_is_404(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    assert client.get("/api/analysis/99999").status_code == 404


# ── Monte Carlo summary ──


def test_monte_carlo_summary(tmp_path) -> None:
    client, ids = make_client(tmp_path)
    resp = client.get(f"/api/analysis/monte-carlo/{ids['mc_id']}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_simulations"] == 1
    assert body["probability_of_loss"] == 0.0
    assert body["final_equity_median"] == 10_050.0


def test_monte_carlo_summary_wrong_type_is_404(tmp_path) -> None:
    """Missing id and wrong result_type are indistinguishable (404)."""
    client, ids = make_client(tmp_path)
    assert (
        client.get(
            f"/api/analysis/monte-carlo/{ids['backtest_id']}/summary"
        ).status_code
        == 404
    )
    assert client.get("/api/analysis/monte-carlo/99999/summary").status_code == 404


# ── Safety: read-only + failure behavior ──


def test_d2_routes_reject_writes(tmp_path) -> None:
    client, _ = make_client(tmp_path)
    for method in ("post", "put", "patch", "delete"):
        for path in ("/api/trades", "/api/orders", "/api/performance/live"):
            resp = getattr(client, method)(path)
            assert resp.status_code == 405


def test_d2_routes_503_on_corrupt_db(tmp_path) -> None:
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"\x00\x01not a sqlite database\xff\xfe")
    client = TestClient(create_app(db_path=str(bad)))
    for path in (
        "/api/trades",
        "/api/orders",
        "/api/fills",
        "/api/performance/live",
        "/api/performance/backtest",
        "/api/analysis",
    ):
        assert client.get(path).status_code == 503
