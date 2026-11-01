import { api } from "./client";
import type {
  AnalysisDetailResponse,
  AnalysisSummaryResponse,
  BacktestPerformanceResponse,
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
} from "@/types";

export const getHealth = () => api.get<HealthResponse>("/api/health");
export const getOverview = (sessionId?: string) =>
  api.get<OverviewResponse>("/api/overview", { session_id: sessionId ?? null });
export const getSessions = (limit = 50) => api.get<SessionResponse[]>("/api/sessions", { limit });
export const getSession = (id: string) => api.get<SessionResponse>(`/api/sessions/${encodeURIComponent(id)}`);

export const getTrades = (p: { symbol?: string; side?: string; session_id?: string; start_time?: string; end_time?: string; limit?: number } = {}) =>
  api.get<TradeResponse[]>("/api/trades", p);
export const getOrders = (p: { session_id?: string; limit?: number } = {}) => api.get<OrderResponse[]>("/api/orders", p);
export const getFills = (p: { session_id?: string; limit?: number } = {}) => api.get<FillResponse[]>("/api/fills", p);

export const getLivePerformance = (p: { session_id?: string; symbol?: string } = {}) =>
  api.get<LivePerformanceResponse>("/api/performance/live", p);
export const getBacktestPerformance = (p: { strategy_name?: string; symbol?: string } = {}) =>
  api.get<BacktestPerformanceResponse>("/api/performance/backtest", p);

export const getAnalysis = (p: { result_type?: string; strategy_name?: string; symbol?: string; limit?: number } = {}) =>
  api.get<AnalysisSummaryResponse[]>("/api/analysis", p);
export const getAnalysisDetail = (id: number) => api.get<AnalysisDetailResponse>(`/api/analysis/${id}`);
export const getMonteCarloSummary = (id: number) =>
  api.get<MonteCarloSummaryResponse>(`/api/analysis/monte-carlo/${id}/summary`);

export const getRiskOverview = (sessionId?: string) =>
  api.get<RiskOverviewResponse>("/api/risk/overview", { session_id: sessionId ?? null });
export const getKillStatus = (symbol?: string) =>
  api.get<KillStatusResponse>("/api/risk/kill-status", { symbol: symbol ?? null });
export const getRiskState = (p: { symbol?: string; mode?: string; day?: string } = {}) =>
  api.get<RiskStateResponse>("/api/risk/state", p);