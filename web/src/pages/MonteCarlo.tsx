import { useState } from "react";
import { useAnalysis, useMonteCarloSummary } from "@/api/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Metric } from "@/components/Metric";

export function MonteCarlo() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const list = useAnalysis({ result_type: "monte_carlo" });
  const summary = useMonteCarloSummary(selectedId ?? 0);
  if (list.isLoading) return <p>Loading…</p>;
  if (list.error) return <ErrorState error={list.error} />;

  const rows = list.data ?? [];
  if (rows.length === 0) return <EmptyState message="No Monte Carlo results persisted yet." />;

  return (
    <section>
      <h1>Monte Carlo Simulation</h1>
      <table>
        <thead><tr><th>ID</th><th>Strategy</th><th>Symbol</th><th>Created</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => setSelectedId(r.id)} className="row">
              <td>{r.id}</td><td>{r.strategy_name}</td><td>{r.symbol}</td><td>{r.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {selectedId !== null ? (
        <div className="detail">
          <h2>Result {selectedId}</h2>
          {summary.isLoading ? <p>Loading…</p> : null}
          {summary.error ? <ErrorState error={summary.error} /> : null}
          {summary.data ? (
            <div className="metrics">
              <Metric label="Simulations" value={summary.data.num_simulations} />
              <Metric label="Method" value={summary.data.method} />
              <Metric label="Initial capital" value={summary.data.initial_capital} />
              <Metric label="Original final equity" value={summary.data.original_final_equity} />
              <Metric label="Original max drawdown" value={`${(summary.data.original_max_drawdown * 100).toFixed(1)}%`} />
              <Metric label="Probability of loss" value={`${(summary.data.probability_of_loss * 100).toFixed(1)}%`} />
              <Metric label="Final equity p5" value={summary.data.final_equity_p5} />
              <Metric label="Final equity median" value={summary.data.final_equity_median} />
              <Metric label="Final equity p95" value={summary.data.final_equity_p95} />
              <Metric label="Final equity mean" value={summary.data.final_equity_mean} />
              <Metric label="Final equity worst" value={summary.data.final_equity_worst} />
              <Metric label="Final equity best" value={summary.data.final_equity_best} />
              <Metric label="Median max drawdown" value={`${(summary.data.median_max_drawdown * 100).toFixed(1)}%`} />
              <Metric label="Worst max drawdown" value={`${(summary.data.worst_max_drawdown * 100).toFixed(1)}%`} />
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}