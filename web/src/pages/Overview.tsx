import { useHealth, useOverview } from "@/api/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Metric } from "@/components/Metric";

export function Overview() {
  const health = useHealth();
  const overview = useOverview();
  if (health.isLoading || overview.isLoading) return <p>Loading…</p>;
  if (health.error) return <ErrorState error={health.error} />;
  if (overview.error) return <ErrorState error={overview.error} />;
  const o = overview.data!;
  return (
    <section>
      <h1>Overview</h1>
      <p className="muted">Read-only. Schema version: {health.data?.schema_version ?? "—"}</p>
      <div className="metrics">
        <Metric label="Total trades" value={o.num_trades} />
        <Metric label="Winning" value={o.num_winning} />
        <Metric label="Losing" value={o.num_losing} />
        <Metric label="Win rate" value={`${(o.win_rate * 100).toFixed(1)}%`} />
        <Metric label="Total PnL" value={o.total_pnl.toFixed(2)} />
        <Metric label="Avg trade PnL" value={o.avg_trade_pnl.toFixed(4)} />
      </div>
      <h2>Current / most recent session</h2>
      {o.current_session === null ? (
        <EmptyState message="No trading sessions recorded yet." />
      ) : (
        <dl className="session">
          <dt>Status</dt><dd>{o.current_session.ended_at === null ? "Open (not proof of running)" : "Ended"}</dd>
          <dt>Symbol</dt><dd>{o.current_session.symbol}</dd>
          <dt>Strategy</dt><dd>{o.current_session.strategy}</dd>
          <dt>Mode</dt><dd>{o.current_session.mode}</dd>
        </dl>
      )}
      <h2>Recent trades</h2>
      {o.recent_trades.length === 0 ? (
        <EmptyState message="No trades recorded yet." />
      ) : (
        <table>
          <thead><tr><th>Session</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL</th></tr></thead>
          <tbody>
            {o.recent_trades.map((t) => (
              <tr key={t.id}>
                <td>{t.session_id}</td><td>{t.side}</td><td>{t.entry_price}</td><td>{t.exit_price}</td><td>{t.pnl}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}