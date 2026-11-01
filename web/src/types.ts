// Hand-mirrored from src/api/schemas.py. Keep in sync with backend schemas.
export interface HealthResponse {
  status: "ok";
  schema_version: number;
}

export interface SessionResponse {
  session_id: string;
  symbol: string;
  strategy: string;
  mode: string;
  started_at: string;
  ended_at: string | null;
  starting_balance: number | null;
  ending_balance: number | null;
  total_pnl: number | null;
  num_trades: number | null;
}

export interface TradeResponse {
  id: number;
  session_id: string;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  size: number;
  pnl: number;
  pnl_pct: number;
  entry_time: string | null;
  exit_time: string;
}

export interface OrderResponse {
  id: number;
  session_id: string;
  symbol: string;
  side: string;
  price: number;
  size: number;
  order_type: string | null;
  oid: number | null;
  cloid: string | null;
  created_at: string;
}

export interface FillResponse {
  id: number;
  session_id: string;
  symbol: string;
  side: string;
  price: number;
  size: number;
  fee: number;
  oid: number | null;
  cloid: string | null;
  filled_at: string;
}

export interface LivePerformanceResponse {
  num_trades: number;
  total_pnl: number;
  win_rate: number;
  profit_factor: number | null;
  expectancy: number;
  sharpe_ratio: number | null;
  max_drawdown: number;
  equity_curve: (number | null)[];
}

export interface BacktestPerformanceResponse {
  result: Record<string, unknown>;
  report: Record<string, unknown>;
}

export interface AnalysisSummaryResponse {
  id: number;
  result_type: string;
  strategy_name: string;
  symbol: string;
  created_at: string;
}

export interface AnalysisDetailResponse {
  id: number;
  result_type: string;
  strategy_name: string;
  symbol: string;
  created_at: string;
  summary: Record<string, unknown>;
  aggregates: Record<string, unknown> | null;
}

export interface MonteCarloSummaryResponse {
  num_simulations: number;
  method: string;
  initial_capital: number;
  original_final_equity: number;
  original_max_drawdown: number;
  final_equity_p5: number;
  final_equity_p25: number;
  final_equity_median: number;
  final_equity_p75: number;
  final_equity_p95: number;
  final_equity_mean: number;
  final_equity_worst: number;
  final_equity_best: number;
  median_max_drawdown: number;
  worst_max_drawdown: number;
  probability_of_loss: number;
}

export interface OverviewResponse {
  num_trades: number;
  num_winning: number;
  num_losing: number;
  win_rate: number;
  total_pnl: number;
  avg_trade_pnl: number;
  recent_trades: TradeResponse[];
  current_session: SessionResponse | null;
}

export interface ErrorResponse {
  detail: string;
}

export interface RiskOverviewResponse {
  configured_max_position_pct: number;
  configured_max_daily_loss_pct: number;
  configured_max_daily_loss_usdc: number;
  configured_max_open_positions: number;
  configured_position_size: number;
  today_realized_loss_usdc: number;
  today_loss_pct_of_cap: number;
}

export interface KillFileStatus {
  present: boolean;
  reason: string | null;
  unverifiable: boolean;
}

export interface KillModeStatus {
  kill_engaged: boolean;
  kill_reason: string | null;
  realized_loss: number | null;
  open_qty: number | null;
  entry_price: number | null;
  total_pnl: number | null;
  num_trades: number | null;
  session_id: string | null;
  updated_at: string | null;
  corrupt: boolean;
  row: Record<string, unknown> | null;
  unverifiable: boolean;
}

export interface KillStatusResponse {
  symbol: string;
  day: string;
  file: KillFileStatus;
  modes: Record<string, KillModeStatus | null>;
  blocked: boolean;
  block_reason: string | null;
}

export interface RiskStateResponse {
  symbol: string;
  mode: string;
  day: string;
  session_id: string;
  realized_loss: number | null;
  open_qty: number | null;
  entry_price: number | null;
  entry_fee: number | null;
  basis_known: boolean;
  total_pnl: number | null;
  num_trades: number | null;
  kill_engaged: boolean;
  kill_reason: string | null;
  updated_at: string | null;
  corrupt: boolean;
}

export type ApiErrorKind = "not_found" | "validation" | "unavailable" | "network" | "timeout";

export class ApiError extends Error {
  kind: ApiErrorKind;
  status: number;
  constructor(kind: ApiErrorKind, status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}