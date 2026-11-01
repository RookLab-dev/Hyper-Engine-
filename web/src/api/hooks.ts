import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  getAnalysis, getAnalysisDetail, getBacktestPerformance, getFills, getHealth,
  getKillStatus, getLivePerformance, getMonteCarloSummary, getOrders, getOverview,
  getRiskOverview, getRiskState, getSession, getSessions, getTrades,
} from "./endpoints";
import type {
  HealthResponse, OverviewResponse, SessionResponse,
} from "@/types";

export function useHealth(): UseQueryResult<HealthResponse> { return useQuery({ queryKey: ["health"], queryFn: getHealth, refetchInterval: 30_000 }); }
export function useOverview(sessionId?: string): UseQueryResult<OverviewResponse> { return useQuery({ queryKey: ["overview", sessionId ?? null], queryFn: () => getOverview(sessionId), refetchInterval: 15_000 }); }
export function useSessions(limit = 50): UseQueryResult<SessionResponse[]> { return useQuery({ queryKey: ["sessions", limit], queryFn: () => getSessions(limit), refetchInterval: 15_000 }); }
export function useSession(id: string): UseQueryResult<SessionResponse> { return useQuery({ queryKey: ["session", id], queryFn: () => getSession(id), refetchInterval: 15_000 }); }
export function useTrades(p: Parameters<typeof getTrades>[0] = {}) { return useQuery({ queryKey: ["trades", p], queryFn: () => getTrades(p) }); }
export function useRiskOverview(sessionId?: string) { return useQuery({ queryKey: ["riskOverview", sessionId ?? null], queryFn: () => getRiskOverview(sessionId) }); }
export function useKillStatus(symbol?: string) { return useQuery({ queryKey: ["killStatus", symbol ?? null], queryFn: () => getKillStatus(symbol) }); }
export function useRiskState(p: Parameters<typeof getRiskState>[0] = {}) { return useQuery({ queryKey: ["riskState", p], queryFn: () => getRiskState(p) }); }
export function useOrders(p: Parameters<typeof getOrders>[0] = {}) { return useQuery({ queryKey: ["orders", p], queryFn: () => getOrders(p) }); }
export function useFills(p: Parameters<typeof getFills>[0] = {}) { return useQuery({ queryKey: ["fills", p], queryFn: () => getFills(p) }); }
export function useLivePerformance(p: Parameters<typeof getLivePerformance>[0] = {}) { return useQuery({ queryKey: ["livePerf", p], queryFn: () => getLivePerformance(p) }); }
export function useBacktestPerformance(p: Parameters<typeof getBacktestPerformance>[0] = {}) { return useQuery({ queryKey: ["backtestPerf", p], queryFn: () => getBacktestPerformance(p) }); }
export function useAnalysis(p: Parameters<typeof getAnalysis>[0] = {}) { return useQuery({ queryKey: ["analysis", p], queryFn: () => getAnalysis(p) }); }
export function useAnalysisDetail(id: number) { return useQuery({ queryKey: ["analysisDetail", id], queryFn: () => getAnalysisDetail(id) }); }
export function useMonteCarloSummary(id: number) { return useQuery({ queryKey: ["mcSummary", id], queryFn: () => getMonteCarloSummary(id) }); }