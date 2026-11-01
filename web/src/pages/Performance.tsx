import { useBacktestPerformance, useLivePerformance } from "@/api/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Metric } from "@/components/Metric";
import { ApiError } from "@/types";

function infinity(value: number | null): string { return value === null ? "∞" : String(value); }

export function Performance() {
  const live = useLivePerformance({});
  const bt = useBacktestPerformance({});
  if (live.isLoading || bt.isLoading) return <p>Loading…</p>;
  if (live.error) return <ErrorState error={live.error} />;
  if (
      bt.error &&
      !(bt.error instanceof ApiError && bt.error.kind === "not_found" && bt.error.status === 404)
    ) {
      return <ErrorState error={bt.error} />;
    }

  const lp = live.data!;
  const bp = bt.data ?? null;

  return (
    <section>
      <h1>Performance</h1>
      <h2>Live / Paper Trades</h2>
      {lp.num_trades === 0 ? <EmptyState message="No persisted trades yet." /> : (
        <>
          <div className="metrics">
            <Metric label="Trades" value={lp.num_trades} />
            <Metric label="Total PnL" value={lp.total_pnl.toFixed(2)} />
            <Metric label="Win rate" value={`${(lp.win_rate * 100).toFixed(1)}%`} />
            <Metric label="Profit factor" value={infinity(lp.profit_factor)} />
            <Metric label="Expectancy" value={lp.expectancy.toFixed(4)} />
            <Metric label="Sharpe" value={lp.sharpe_ratio === null ? "—" : lp.sharpe_ratio.toFixed(4)} />
            <Metric label="Max drawdown" value={`${(lp.max_drawdown * 100).toFixed(1)}%`} />
          </div>
          <h3>Equity curve (trade-close-stepped, not mark-to-market)</h3>
          <svg width="100%" height="120" viewBox="0 0 400 120" preserveAspectRatio="none">
            {lp.equity_curve.length > 1 ? (
              <polyline
                fill="none"
                stroke="#2563eb"
                strokeWidth="2"
                points={lp.equity_curve
                  .map((v, i) => (v === null ? null : `${(i / (lp.equity_curve.length - 1)) * 400},${120 - (v as number)}`))
                  .filter((p): p is string => p !== null)
                  .join(" ")}
              />
            ) : null}
          </svg>
        </>
      )}
      <h2>Latest Backtest</h2>
      {bp === null ? <EmptyState message="No backtest results persisted yet." /> : (
        <>
          <div className="metrics">
            <Metric label="Trades" value={typeof bp.report?.num_trades === "number" ? bp.report.num_trades : "—"} />
            <Metric label="Total PnL" value={typeof bp.report?.total_pnl === "number" ? (bp.report.total_pnl as number).toFixed(2) : "—"} />
            <Metric label="Win rate" value={typeof bp.report?.win_rate === "number" ? `${((bp.report.win_rate as number) * 100).toFixed(1)}%` : "—"} />
            <Metric label="Sharpe" value={typeof bp.report?.sharpe_ratio === "number" ? (bp.report.sharpe_ratio as number).toFixed(4) : "—"} />
            <Metric label="Max drawdown" value={typeof bp.report?.max_drawdown === "number" ? `${((bp.report.max_drawdown as number) * 100).toFixed(1)}%` : "—"} />
          </div>
          {Array.isArray(bp.report?.equity_curve) && (bp.report.equity_curve as number[]).length > 0 ? (
            <>
              <h3>Equity curve</h3>
              <svg width="100%" height="120" viewBox="0 0 400 120" preserveAspectRatio="none">
                <polyline fill="none" stroke="#16a34a" strokeWidth="2" points={
                  (bp.report.equity_curve as number[])
                    .map((v, i, a) => `${(i / (a.length - 1)) * 400},${120 - v}`)
                    .join(" ")
                } />
              </svg>
            </>
          ) : null}
        </>
      )}
    </section>
  );
}