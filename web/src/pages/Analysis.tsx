import { useState } from "react";
import { useAnalysis, useAnalysisDetail } from "@/api/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";

export function Analysis() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const list = useAnalysis({});
  const detail = useAnalysisDetail(selectedId ?? 0);
  if (list.isLoading) return <p>Loading…</p>;
  if (list.error) return <ErrorState error={list.error} />;

  const rows = list.data ?? [];

  return (
    <section>
      <h1>Strategy Analysis</h1>
      {rows.length === 0 ? <EmptyState message="No analysis results persisted yet." /> : (
        <>
          <table>
            <thead><tr><th>ID</th><th>Type</th><th>Strategy</th><th>Symbol</th><th>Created</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} onClick={() => setSelectedId(r.id)} className="row">
                  <td>{r.id}</td><td>{r.result_type}</td><td>{r.strategy_name}</td><td>{r.symbol}</td><td>{r.created_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selectedId !== null ? (
            <div className="detail">
              <h2>Result {selectedId}</h2>
              {selectedId !== null && detail.isLoading ? <p>Loading…</p> : null}
              {selectedId !== null && detail.error ? <ErrorState error={detail.error} /> : null}
              {selectedId !== null && detail.data ? (
                <>
                  <p>Type: {detail.data.result_type}</p>
                  <p>Strategy: {detail.data.strategy_name}</p>
                  <p>Symbol: {detail.data.symbol}</p>
                  {detail.data.aggregates ? (
                    <pre>{JSON.stringify(detail.data.aggregates, null, 2)}</pre>
                  ) : (
                    <p className="muted">No rehydrated aggregates for this result type.</p>
                  )}
                </>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}