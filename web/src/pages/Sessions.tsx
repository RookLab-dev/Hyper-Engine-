import { useState } from "react";
import { useSession, useSessions } from "@/api/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";

export function Sessions() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const sessions = useSessions(50);
  const detail = useSession(selectedId ?? "");
  if (sessions.isLoading) return <p>Loading…</p>;
  if (sessions.error) return <ErrorState error={sessions.error} />;
  const list = sessions.data ?? [];
  return (
    <section>
      <h1>Sessions</h1>
      {list.length === 0 ? (
        <EmptyState message="No trading sessions recorded yet." />
      ) : (
        <>
          <table>
            <thead><tr><th>ID</th><th>Symbol</th><th>Strategy</th><th>Mode</th><th>Started</th><th>Ended</th><th>PnL</th></tr></thead>
            <tbody>
              {list.map((s) => (
                <tr key={s.session_id} onClick={() => setSelectedId(s.session_id)} className="row">
                  <td>{s.session_id}</td><td>{s.symbol}</td><td>{s.strategy}</td><td>{s.mode}</td>
                  <td>{s.started_at}</td><td>{s.ended_at ?? "Open (not proof of running)"}</td><td>{s.total_pnl ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selectedId ? (
            <div className="detail">
              <h2>Session {selectedId}</h2>
              {detail.isLoading ? <p>Loading…</p> : null}
              {detail.error ? <ErrorState error={detail.error} /> : null}
              {detail.data ? (
                <dl>
                  <dt>Symbol</dt><dd>{detail.data.symbol}</dd>
                  <dt>Strategy</dt><dd>{detail.data.strategy}</dd>
                  <dt>Mode</dt><dd>{detail.data.mode}</dd>
                  <dt>Started</dt><dd>{detail.data.started_at}</dd>
                  <dt>Ended</dt><dd>{detail.data.ended_at ?? "Open (not proof of running)"}</dd>
                  <dt>Trades</dt><dd>{detail.data.num_trades ?? "—"}</dd>
                  <dt>Total PnL</dt><dd>{detail.data.total_pnl ?? "—"}</dd>
                </dl>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}