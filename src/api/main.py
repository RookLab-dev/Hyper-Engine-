"""FastAPI application factory (D1).

Read-only: health, sessions, overview (GET only). Constructed via
create_app(db_path=None) so tests inject a tmp database; production
resolves storage/hyper_engine.db (or HYPER_ENGINE_DB_PATH). Localhost
is the expected bind; CORS stays same-origin by default. An open
session (ended_at null) is reported as-is — it is NOT proof the bot
is running.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api import deps
from api.schemas import (
    AnalysisDetailResponse,
    AnalysisSummaryResponse,
    BacktestPerformanceResponse,
    ErrorResponse,
    FillResponse,
    HealthResponse,
    KillStatusResponse,
    LivePerformanceResponse,
    MonteCarloSummaryResponse,
    OrderResponse,
    OverviewResponse,
    RiskOverviewResponse,
    RiskStateResponse,
    SessionResponse,
    TradeResponse,
)
from core.config import settings
from core.database import Database
from dashboard import data as dashboard_data

ERROR_503_MODEL: dict[int | str, dict[str, Any]] = {503: {"model": ErrorResponse}}
ERROR_404_MODEL: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse}}


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="Hyper-Engine API", version="0.1.0")
    app.state.db_path = db_path

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_methods=["GET"],
        allow_headers=[],
    )

    @app.get("/api/health", response_model=HealthResponse, responses=ERROR_503_MODEL)
    def health(db: Database = Depends(deps.get_db)) -> HealthResponse:
        return HealthResponse(status="ok", schema_version=db.get_schema_version())

    @app.get(
        "/api/sessions",
        response_model=list[SessionResponse],
        responses=ERROR_503_MODEL,
    )
    def list_sessions(
        limit: int = Query(default=50, ge=1, le=500),
        db: Database = Depends(deps.get_db),
    ) -> list[SessionResponse]:
        return [SessionResponse(**s) for s in db.list_sessions(limit=limit)]

    @app.get(
        "/api/sessions/{session_id}",
        response_model=SessionResponse,
        responses={**ERROR_404_MODEL, **ERROR_503_MODEL},
    )
    def get_session(
        session_id: str, db: Database = Depends(deps.get_db)
    ) -> SessionResponse:
        session = db.get_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=404, detail=f"Session not found: {session_id}"
            )
        return SessionResponse(**session)

    @app.get(
        "/api/overview",
        response_model=OverviewResponse,
        responses=ERROR_503_MODEL,
    )
    def overview(
        session_id: str | None = Query(default=None),
        db: Database = Depends(deps.get_db),
    ) -> OverviewResponse:
        return OverviewResponse(**dashboard_data.get_overview(db, session_id))

    @app.get(
        "/api/trades",
        response_model=list[TradeResponse],
        responses=ERROR_503_MODEL,
    )
    def list_trades(
        symbol: str | None = Query(default=None),
        side: str | None = Query(default=None),
        session_id: str | None = Query(default=None),
        start_time: str | None = Query(default=None),
        end_time: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=500),
        db: Database = Depends(deps.get_db),
    ) -> list[TradeResponse]:
        return [
            TradeResponse(**t)
            for t in dashboard_data.list_trades_view(
                db,
                symbol=symbol,
                side=side,
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )
        ]

    @app.get(
        "/api/orders",
        response_model=list[OrderResponse],
        responses=ERROR_503_MODEL,
    )
    def list_orders(
        session_id: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=500),
        db: Database = Depends(deps.get_db),
    ) -> list[OrderResponse]:
        return [
            OrderResponse(**o)
            for o in dashboard_data.list_orders_view(
                db, session_id=session_id, limit=limit
            )
        ]

    @app.get(
        "/api/fills",
        response_model=list[FillResponse],
        responses=ERROR_503_MODEL,
    )
    def list_fills(
        session_id: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=500),
        db: Database = Depends(deps.get_db),
    ) -> list[FillResponse]:
        return [
            FillResponse(**f)
            for f in dashboard_data.list_fills_view(
                db, session_id=session_id, limit=limit
            )
        ]

    @app.get(
        "/api/performance/live",
        response_model=LivePerformanceResponse,
        responses=ERROR_503_MODEL,
    )
    def live_performance(
        session_id: str | None = Query(default=None),
        symbol: str | None = Query(default=None),
        db: Database = Depends(deps.get_db),
    ) -> LivePerformanceResponse:
        return LivePerformanceResponse(
            **dashboard_data.get_live_trades_performance(
                db, session_id=session_id, symbol=symbol
            )
        )

    @app.get(
        "/api/performance/backtest",
        response_model=BacktestPerformanceResponse,
        responses={**ERROR_404_MODEL, **ERROR_503_MODEL},
    )
    def backtest_performance(
        strategy_name: str | None = Query(default=None),
        symbol: str | None = Query(default=None),
        db: Database = Depends(deps.get_db),
    ) -> BacktestPerformanceResponse:
        summary = dashboard_data.get_latest_backtest_performance(
            db, strategy_name=strategy_name, symbol=symbol
        )
        if summary is None:
            raise HTTPException(
                status_code=404, detail="No backtest results persisted yet."
            )
        return BacktestPerformanceResponse(
            result=summary["result"], report=summary["report"]
        )

    @app.get(
        "/api/analysis",
        response_model=list[AnalysisSummaryResponse],
        responses=ERROR_503_MODEL,
    )
    def list_analysis(
        result_type: str | None = Query(default=None),
        strategy_name: str | None = Query(default=None),
        symbol: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        db: Database = Depends(deps.get_db),
    ) -> list[AnalysisSummaryResponse]:
        if result_type is not None and not result_type.strip():
            raise HTTPException(
                status_code=422, detail="result_type must not be blank."
            )
        return [
            AnalysisSummaryResponse(**r)
            for r in dashboard_data.list_analysis_results_view(
                db,
                result_type=result_type,
                strategy_name=strategy_name,
                symbol=symbol,
                limit=limit,
            )
        ]

    @app.get(
        "/api/analysis/{result_id}",
        response_model=AnalysisDetailResponse,
        responses={**ERROR_404_MODEL, **ERROR_503_MODEL},
    )
    def get_analysis(
        result_id: int, db: Database = Depends(deps.get_db)
    ) -> AnalysisDetailResponse:
        detail = dashboard_data.get_analysis_result_detail(db, result_id)
        if detail is None:
            raise HTTPException(
                status_code=404, detail=f"Analysis result not found: {result_id}"
            )
        aggregates: dict | None = None
        rehydrated = detail.get("rehydrated")
        if rehydrated is not None:
            if detail["result_type"] == "monte_carlo":
                summary = dashboard_data.get_monte_carlo_summary(db, result_id)
                aggregates = dict(summary) if summary is not None else None
            elif detail["result_type"] == "walk_forward":
                # Exactly the scalar properties the Streamlit walk-forward
                # page displays (strategy_analysis._render_walk_forward).
                # combined_test_equity_curve() is deliberately excluded:
                # it requires an initial_capital argument the stored
                # report does not carry, and the dashboard never calls it.
                aggregates = {
                    "num_windows": rehydrated.num_windows,
                    "total_test_trades": rehydrated.total_test_trades,
                    "total_test_pnl": rehydrated.total_test_pnl,
                    "average_test_sharpe": rehydrated.average_test_sharpe,
                    "average_test_win_rate": rehydrated.average_test_win_rate,
                    "profitable_window_count": rehydrated.profitable_window_count,
                }
        return AnalysisDetailResponse(
            id=detail["id"],
            result_type=detail["result_type"],
            strategy_name=detail["strategy_name"],
            symbol=detail["symbol"],
            created_at=detail["created_at"],
            summary=detail["summary"],
            aggregates=aggregates,
        )

    @app.get(
        "/api/analysis/monte-carlo/{result_id}/summary",
        response_model=MonteCarloSummaryResponse,
        responses={**ERROR_404_MODEL, **ERROR_503_MODEL},
    )
    def monte_carlo_summary(
        result_id: int, db: Database = Depends(deps.get_db)
    ) -> MonteCarloSummaryResponse:
        summary = dashboard_data.get_monte_carlo_summary(db, result_id)
        if summary is None:
            raise HTTPException(
                status_code=404,
                detail=f"Monte Carlo result not found: {result_id}",
            )
        return MonteCarloSummaryResponse(**summary)

    @app.get(
        "/api/risk/overview",
        response_model=RiskOverviewResponse,
        responses=ERROR_503_MODEL,
    )
    def risk_overview(
        session_id: str | None = Query(default=None),
        db: Database = Depends(deps.get_db),
    ) -> RiskOverviewResponse:
        return RiskOverviewResponse(
            **dashboard_data.get_risk_overview(db, session_id=session_id)
        )

    @app.get(
        "/api/risk/kill-status",
        response_model=KillStatusResponse,
        responses=ERROR_503_MODEL,
    )
    def kill_status(
        symbol: str | None = Query(default=None),
        db: Database = Depends(deps.get_db),
    ) -> KillStatusResponse:
        return KillStatusResponse(
            **dashboard_data.get_kill_status(
                db, symbol=symbol, kill_path=deps.resolve_kill_path()
            )
        )

    @app.get(
        "/api/risk/state",
        response_model=RiskStateResponse,
        responses={**ERROR_404_MODEL, **ERROR_503_MODEL},
    )
    def risk_state(
        symbol: str | None = Query(default=None),
        mode: str = Query(default="paper_trade"),
        day: str | None = Query(default=None),
        db: Database = Depends(deps.get_db),
    ) -> RiskStateResponse:
        from core.risk_state import RiskState, utc_today

        if mode not in ("paper_trade", "testnet_live_execution", "smoke_test"):
            raise HTTPException(status_code=422, detail=f"Unknown risk mode: {mode}")
        resolved_symbol = symbol or settings.symbol
        resolved_day = day or utc_today()
        row = db.load_risk_state(resolved_symbol, mode, resolved_day)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(f"No risk state for {resolved_symbol}/{mode}/{resolved_day}."),
            )
        try:
            RiskState.from_row(row)
        except ValueError:
            corrupt = dict(row)
            corrupt["corrupt"] = True
            return RiskStateResponse(**corrupt)
        return RiskStateResponse(**row)

    return app


app = create_app()
