import { useFills, useOrders, useTrades } from "@/api/hooks";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";

export function Trades() {
  const trades = useTrades({});
  const orders = useOrders({});
  const fills = useFills({});
  if (trades.isLoading || orders.isLoading || fills.isLoading) return <p>Loading…</p>;
  if (trades.error) return <ErrorState error={trades.error} />;
  if (orders.error) return <ErrorState error={orders.error} />;
  if (fills.error) return <ErrorState error={fills.error} />;

  const tradeRows = trades.data ?? [];
  const orderRows = orders.data ?? [];
  const fillRows = fills.data ?? [];

  return (
    <section>
      <h1>Trades, Orders &amp; Fills</h1>
      <p className="muted">Persisted historical records only — not a live order-status feed.</p>
      <h2>Trades</h2>
      {tradeRows.length === 0 ? <EmptyState message="No trades recorded yet." /> : (
        <table><thead><tr><th>ID</th><th>Session</th><th>Side</th><th>Entry</th><th>Exit</th><th>PnL</th></tr></thead>
          <tbody>{tradeRows.map((t) => <tr key={t.id}><td>{t.id}</td><td>{t.session_id}</td><td>{t.side}</td><td>{t.entry_price}</td><td>{t.exit_price}</td><td>{t.pnl}</td></tr>)}</tbody></table>
      )}
      <h2>Orders</h2>
      {orderRows.length === 0 ? <EmptyState message="No orders recorded yet." /> : (
        <table><thead><tr><th>ID</th><th>Session</th><th>Side</th><th>Price</th><th>Size</th><th>Type</th></tr></thead>
          <tbody>{orderRows.map((o) => <tr key={o.id}><td>{o.id}</td><td>{o.session_id}</td><td>{o.side}</td><td>{o.price}</td><td>{o.size}</td><td>{o.order_type ?? "—"}</td></tr>)}</tbody></table>
      )}
      <h2>Fills</h2>
      {fillRows.length === 0 ? <EmptyState message="No fills recorded yet." /> : (
        <table><thead><tr><th>ID</th><th>Session</th><th>Side</th><th>Price</th><th>Size</th><th>Fee</th></tr></thead>
          <tbody>{fillRows.map((f) => <tr key={f.id}><td>{f.id}</td><td>{f.session_id}</td><td>{f.side}</td><td>{f.price}</td><td>{f.size}</td><td>{f.fee}</td></tr>)}</tbody></table>
      )}
    </section>
  );
}