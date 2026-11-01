import { useKillStatus, useRiskOverview, useRiskState } from "@/api/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Metric } from "@/components/Metric";

export function Risk() {
  const overview = useRiskOverview();
  const kill = useKillStatus();
  const state = useRiskState({});
  if (overview.isLoading || kill.isLoading || state.isLoading) return <p>Loading…</p>;
  if (overview.error) return <ErrorState error={overview.error} />;
  if (kill.error) return <ErrorState error={kill.error} />;
  if (state.error && (state.error as { status?: number }).status !== 404) return <ErrorState error={state.error} />;

  const o = overview.data!;
  const k = kill.data!;
  const s = state.data ?? null;

  return (
    <section>
      <h1>Risk (Read-Only)</h1>
      {k.blocked ? <div className="banner error">BLOCKED: {k.block_reason ?? "unknown"}</div> : <div className="banner ok">Not blocked</div>}
      <div className="metrics">
        <Metric label="Max position %" value={`${(o.configured_max_position_pct * 100).toFixed(1)}%`} />
        <Metric label="Max daily loss %" value={`${(o.configured_max_daily_loss_pct * 100).toFixed(1)}%`} />
        <Metric label="Max daily loss USDC" value={o.configured_max_daily_loss_usdc.toFixed(2)} />
        <Metric label="Max open positions" value={o.configured_max_open_positions} />
        <Metric label="Position size" value={o.configured_position_size} />
        <Metric label="Today realized loss (display estimate)" value={o.today_realized_loss_usdc.toFixed(2)} />
        <Metric label="Today loss % of cap" value={`${(o.today_loss_pct_of_cap * 100).toFixed(1)}%`} />
      </div>
      <p className="muted small">Today's realized loss is a display estimate from persisted trades, not the risk gate's authoritative counter.</p>

      <h2>Kill-switch file</h2>
      <p>{k.file.unverifiable ? "UNVERIFIABLE" : k.file.present ? `Engaged (${k.file.reason ?? "UNKNOWN"})` : "Absent (clear)"}</p>

      <h2>Persisted risk state by mode</h2>
      {Object.keys(k.modes).length === 0 ? <p className="muted">No modes reported.</p> : (
        <table>
          <thead><tr><th>Mode</th><th>Engaged</th><th>Reason</th><th>Realized loss</th><th>Open qty</th><th>Total PnL</th><th>Trades</th><th>State</th></tr></thead>
          <tbody>
            {Object.entries(k.modes).map(([mode, m]) => {
              const status = m === null ? "absent" : m.unverifiable ? "unverifiable" : m.corrupt ? "corrupt" : "ok";
              return (
                <tr key={mode}>
                  <td>{mode}</td>
                  <td>{m === null ? "—" : m.kill_engaged ? "YES" : "no"}</td>
                  <td>{m === null ? "—" : m.kill_reason ?? "—"}</td>
                  <td>{m === null || m.realized_loss === null ? "—" : m.realized_loss}</td>
                  <td>{m === null || m.open_qty === null ? "—" : m.open_qty}</td>
                  <td>{m === null || m.total_pnl === null ? "—" : m.total_pnl}</td>
                  <td>{m === null || m.num_trades === null ? "—" : m.num_trades}</td>
                  <td>{status}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <h2>Persisted risk-state row</h2>
      {state.error ? <ErrorState error={state.error} /> : null}
      {s === null ? <EmptyState message="No persisted risk-state row for the requested symbol/mode/day." /> : (
        <dl>
          <dt>Symbol</dt><dd>{s.symbol}</dd>
          <dt>Mode</dt><dd>{s.mode}</dd>
          <dt>Day</dt><dd>{s.day}</dd>
          <dt>Session</dt><dd>{s.session_id}</dd>
          <dt>Kill engaged</dt><dd>{s.kill_engaged ? "YES" : "no"}</dd>
          <dt>Realized loss</dt><dd>{s.realized_loss ?? "—"}</dd>
          <dt>Open qty</dt><dd>{s.open_qty ?? "—"}</dd>
          <dt>Entry price</dt><dd>{s.entry_price ?? "—"}</dd>
          <dt>Total PnL</dt><dd>{s.total_pnl ?? "—"}</dd>
          <dt>Trades</dt><dd>{s.num_trades ?? "—"}</dd>
          <dt>Basis known</dt><dd>{s.basis_known ? "yes" : "no"}</dd>
          {s.corrupt ? <><dt>Corrupt</dt><dd>YES — raw stored values shown, not repaired</dd></> : null}
        </dl>
      )}
    </section>
  );
}