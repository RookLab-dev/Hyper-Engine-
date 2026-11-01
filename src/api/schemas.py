"""Response schemas for the read-only API (D1).

Pydantic models mirroring the dict shapes returned by
src/dashboard/data.py and core.database read methods. Endpoints
construct these directly from those dicts (no new SQL, no
recalculated metrics). An open session (ended_at null) means the
session row was never closed — it is NOT proof the loop is running.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, model_validator


def sanitize_json_floats(value: Any) -> Any:
    """Recursively maps non-finite floats (inf, -inf, NaN) to None.

    The engine legitimately produces inf (e.g.
    PerformanceAnalyser.profit_factor returns inf when there are no
    losing trades), but strict JSON encoders reject out-of-range
    floats. Mapping to null preserves the distinction from any finite
    number without inventing one — never 0.0, never a placeholder.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: sanitize_json_floats(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json_floats(item) for item in value]
    return value


class HealthResponse(BaseModel):
    status: Literal["ok"]
    schema_version: int


class SessionResponse(BaseModel):
    session_id: str
    symbol: str
    strategy: str
    mode: str
    started_at: str
    ended_at: str | None = None
    starting_balance: float | None = None
    ending_balance: float | None = None
    total_pnl: float | None = None
    num_trades: int | None = None


class TradeResponse(BaseModel):
    id: int
    session_id: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    pnl_pct: float
    entry_time: str | None = None
    exit_time: str


class OverviewResponse(BaseModel):
    num_trades: int
    num_winning: int
    num_losing: int
    win_rate: float
    total_pnl: float
    avg_trade_pnl: float
    recent_trades: list[TradeResponse]
    current_session: SessionResponse | None = None


class ErrorResponse(BaseModel):
    detail: str


class OrderResponse(BaseModel):
    """Persisted historical order — read-only, not a live order-status feed."""

    id: int
    session_id: str
    symbol: str
    side: str
    price: float
    size: float
    order_type: str | None = None
    oid: int | None = None
    cloid: str | None = None
    created_at: str


class FillResponse(BaseModel):
    """Persisted historical fill — read-only, not a live fill feed."""

    id: int
    session_id: str
    symbol: str
    side: str
    price: float
    size: float
    fee: float = 0.0
    oid: int | None = None
    cloid: str | None = None
    filled_at: str


class LivePerformanceResponse(BaseModel):
    """Metrics computed from persisted live/paper trades.

    Equity curve is trade-close-stepped (cumulative summed trade PnLs),
    not mark-to-market.

    Non-finite metric values (e.g. profit_factor is +inf when there
    are no losing trades) are returned as JSON null — the engine
    calculation itself is unchanged, only the API serialization maps
    inf/-inf/NaN to null so responses stay standards-compliant JSON.
    """

    num_trades: int
    total_pnl: float
    win_rate: float
    profit_factor: float | None
    expectancy: float
    sharpe_ratio: float | None
    max_drawdown: float
    equity_curve: list[float | None]

    @model_validator(mode="before")
    @classmethod
    def _sanitize_non_finite(cls, data: Any) -> Any:
        return sanitize_json_floats(data)


class BacktestPerformanceResponse(BaseModel):
    """Most recently persisted backtest result, verbatim.

    `result` and `report` are passed through exactly as computed at
    backtest time and persisted — never recalculated here. Field
    contents (golden-pinned) are intentionally not re-modeled.

    Stored reports legitimately contain non-finite floats (e.g.
    profit_factor is +inf when there were no losing trades); those
    are returned as JSON null — engine data unchanged, only the API
    serialization maps inf/-inf/NaN to null.
    """

    result: dict[str, Any]
    report: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _sanitize_non_finite(cls, data: Any) -> Any:
        return sanitize_json_floats(data)


class AnalysisSummaryResponse(BaseModel):
    """Metadata-only listing row for a stored analysis result."""

    id: int
    result_type: str
    strategy_name: str
    symbol: str
    created_at: str


class AnalysisDetailResponse(BaseModel):
    """Full stored analysis result.

    `summary` is the raw persisted dict. For monte_carlo and
    walk_forward results, `aggregates` carries the existing rehydrated
    computed properties serialized as JSON (computed once at analysis
    time by the report classes, read here — never recomputed).
    Out-of-sample and backtest results need no rehydration: every
    field is a real stored field.

    Stored summaries legitimately contain non-finite floats (e.g.
    profit_factor +inf for all-winning backtests); those are returned
    as JSON null — engine data unchanged, only the API serialization
    maps inf/-inf/NaN to null.
    """

    id: int
    result_type: str
    strategy_name: str
    symbol: str
    created_at: str
    summary: dict[str, Any]
    aggregates: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _sanitize_non_finite(cls, data: Any) -> Any:
        return sanitize_json_floats(data)


class RiskOverviewResponse(BaseModel):
    """Configured limits plus trade-derived display figures.

    `today_realized_loss_usdc` is computed from persisted trades for
    DISPLAY — it is not the risk gate's authoritative in-memory
    counter, and an open session (ended_at null) is not proof the
    loop is running.
    """

    configured_max_position_pct: float
    configured_max_daily_loss_pct: float
    configured_max_daily_loss_usdc: float
    configured_max_open_positions: int
    configured_position_size: float
    today_realized_loss_usdc: float
    today_loss_pct_of_cap: float


class KillFileStatus(BaseModel):
    """Kill-switch file state.

    `present=False` with `reason=None` means absent (clear).
    `unverifiable=True` means the file could not be read — treated as
    blocking, never as clear.
    """

    present: bool = False
    reason: str | None = None
    unverifiable: bool = False


class KillModeStatus(BaseModel):
    """One loop mode's persisted kill state.

    A null entry means no row exists (absent, not engaged).
    `unverifiable=True` means the row could not be read; `corrupt=True`
    carries the raw stored row for inspection (never repaired or
    coerced). Neither is ever reported as confidently clear.
    """

    kill_engaged: bool = False
    kill_reason: str | None = None
    realized_loss: float | None = None
    open_qty: float | None = None
    entry_price: float | None = None
    total_pnl: float | None = None
    num_trades: int | None = None
    session_id: str | None = None
    updated_at: str | None = None
    corrupt: bool = False
    row: dict[str, Any] | None = None
    unverifiable: bool = False


class KillStatusResponse(BaseModel):
    """Read-only kill-switch snapshot.

    `blocked`/`block_reason` reuse the engine's authoritative
    kill_block_reason() decision (file first, then per-mode flags with
    prior-day rollover). Any unverifiable representation blocks.
    """

    symbol: str
    day: str
    file: KillFileStatus
    modes: dict[str, KillModeStatus | None]
    blocked: bool
    block_reason: str | None = None


class RiskStateResponse(BaseModel):
    """One persisted risk-state row, verbatim.

    `corrupt=True` means RiskState.from_row() rejected the stored row;
    values are the raw stored values (never repaired, normalized, or
    coerced) with non-finite floats mapped to JSON null at the API
    boundary. A corrupt row is never a healthy row.
    """

    symbol: str
    mode: str
    day: str
    session_id: str
    realized_loss: float | None = None
    open_qty: float | None = None
    entry_price: float | None = None
    entry_fee: float | None = None
    basis_known: bool = False
    total_pnl: float | None = None
    num_trades: int | None = None
    kill_engaged: bool = False
    kill_reason: str | None = None
    updated_at: str | None = None
    corrupt: bool = False

    @model_validator(mode="before")
    @classmethod
    def _sanitize_non_finite(cls, data: Any) -> Any:
        return sanitize_json_floats(data)


class MonteCarloSummaryResponse(BaseModel):
    """Displayable Monte Carlo percentiles and loss probability.

    Every value is read from the existing rehydrated MonteCarloReport
    properties — never recomputed here.
    """

    num_simulations: int
    method: str
    initial_capital: float
    original_final_equity: float
    original_max_drawdown: float
    final_equity_p5: float
    final_equity_p25: float
    final_equity_median: float
    final_equity_p75: float
    final_equity_p95: float
    final_equity_mean: float
    final_equity_worst: float
    final_equity_best: float
    median_max_drawdown: float
    worst_max_drawdown: float
    probability_of_loss: float
