import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Overview } from "@/pages/Overview";
import { Sessions } from "@/pages/Sessions";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

describe("Overview", () => {
  it("shows loading", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ num_trades: 0, num_winning: 0, num_losing: 0, win_rate: 0, total_pnl: 0, avg_trade_pnl: 0, recent_trades: [], current_session: null }) }));
    wrap(<Overview />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
    vi.unstubAllGlobals();
  });
});

describe("Sessions", () => {
  it("shows loading", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => [] }));
    wrap(<Sessions />);
    expect(screen.getByText(/loading/i)).toBeTruthy();
    vi.unstubAllGlobals();
  });
});