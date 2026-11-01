# Hyper-Engine Dashboards

Hyper-Engine provides two read-only interfaces over its persisted trading and analysis data:

* **Web Dashboard:** React + TypeScript frontend backed by the FastAPI read API.
* **Streamlit Dashboard:** Existing Python dashboard backed by the SQLite persistence layer.

Neither interface provides controls to place, cancel, or modify orders. The web dashboard accesses data through the API; the Streamlit dashboard reads through the existing database access layer.

## Web Dashboard

The web frontend lives in `web/` and uses React, TypeScript, Vite, and TanStack Query. Its data comes from the FastAPI application in `src/api/`.

### Start the application

From the repository root, start the API:

```bash
poetry run uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

In another terminal, start the frontend:

```bash
cd web
npm install
npm run dev
```

Open the local URL printed by Vite. Its development server proxies requests beginning with `/api` to `http://127.0.0.1:8000`.

### Frontend checks

Run these commands from the `web/` directory:

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

The production build is written to `web/dist/`.

### Data freshness and interpretation

The frontend uses polling rather than WebSockets. Freshness therefore depends on the configured polling behavior and API availability.

A session whose `ended_at` value is `null` is an open database record; it is not proof that a trading process is currently running.

The Risk view is for observation, not control. Displayed loss figures derived from persisted trades must not be treated as authoritative risk-enforcement counters.


## Streamlit Dashboard: What It Displays

| Page | Data source |
|---|---|
| **Overview** | Most recent session, trade counts, win rate, total/avg PnL, recent trades — win rate and average PnL are computed via `PerformanceAnalyser`, not reimplemented. |
| **Trading History** | Persisted completed trades (`trades` table), filterable by symbol, side, session, and date range. |
| **Orders & Fills** | Persisted historical order/fill records (`orders`/`fills` tables). Explicitly historical — not a live order-status feed. |
| **Performance** | Metrics from `PerformanceAnalyser`, either derived from persisted live/paper trades or read from the most recently persisted backtest result. |
| **Strategy Analysis** | Browses stored `BacktestResult`/`PerformanceReport`, `OutOfSampleReport`, and `WalkForwardReport` results. Nothing is recalculated — every number was computed once at analysis time and persisted as-is. |
| **Monte Carlo** | Stored `MonteCarloReport` results: final-equity percentiles, probability of loss, median/worst drawdown. |
| **Risk** | Configured risk limits (read from `core/config.py` settings — the same config `RiskManager` itself uses) plus a derived "today's realized loss" figure computed from persisted trades. Displays only — cannot change any risk setting. |

## Where the data comes from

The dashboard reads from the **same SQLite database** the rest of Hyper-Engine
writes to via `core.database.Database` (default path: `storage/hyper_engine.db`,
relative to wherever the process runs from). It uses **only** `Database`'s
existing `get_*`/`list_*` read methods — no new tables, no schema changes, no
second database, and no direct SQL of its own.

**Live data is fully wired in.** `paper_trade()`, `live_testnet_trade()`
(both in `strategy_runner.py`), and `execution/smoke_test.py` all construct
`TradeLogger` with `db=Database()`, so every session/order/fill/trade from
any of the three shows up here automatically — no manual persistence call
needed. Analysis results (backtest/OOS/walk-forward/Monte Carlo) are still
persisted explicitly, since they're produced by one-off calls rather than a
running session:

```python
from backtester.performance import PerformanceAnalyser
from core.database import Database

result = backtester.run(candles)
report = PerformanceAnalyser().analyse(result)

db = Database()
db.save_backtest_result(strategy.name, "BTC", result, report)
```

For reference, this is what `paper_trade()`/`live_testnet_trade()`/
`smoke_test.py` already do for you — no extra step required to see a
session here:

```python
trade_log = TradeLogger(log_dir=settings.log_dir, symbol=settings.symbol,
                         strategy=strategy.name, db=Database())
```

## How to start it

The dashboard's dependencies (Streamlit) are **optional** — they aren't
installed by a plain `poetry install`, so the default dev/CI environment stays
exactly as it was before this phase.

```bash
poetry install --with dashboard
poetry run streamlit run src/dashboard/app.py
```

This opens the dashboard in your browser (defaults to `http://localhost:8501`).

## Read-only guarantee

The dashboard **cannot**:
- place, cancel, or modify an order
- change leverage or any risk setting
- modify strategy configuration
- submit anything to Hyperliquid
- write to the database

Every function in `dashboard/data.py` (the only module that touches the
database) calls exclusively `Database`'s existing read methods
(`get_session`, `list_trades`, `list_orders`, `list_fills`,
`list_analysis_results`, `get_analysis_result`) or `PerformanceAnalyser`'s
existing calculation methods. Nothing in `dashboard/` imports
`TestnetExecutor`, `HyperliquidTrading`, `HyperliquidAuth`, or `RiskManager`.

## Code layout

```
src/dashboard/
├── app.py             # Entrypoint: page navigation, wires views to a Database instance
├── data.py            # Pure data layer — no Streamlit import, fully unit-tested
└── views/
    ├── overview.py
    ├── trades.py
    ├── orders_fills.py
    ├── performance.py
    ├── strategy_analysis.py
    ├── monte_carlo.py
    └── risk.py         # each: one render(db) function
```

There is deliberately no `dashboard/dashboard.py` — an earlier attempt at
that filename collided with the top-level `dashboard/` package itself
(Streamlit only puts the *script's own directory* on `sys.path`, not its
parent, so `from dashboard.views import ...` couldn't resolve). `app.py` is
the entrypoint instead, and explicitly inserts `src/` onto `sys.path` before
importing `dashboard.views` — see its module docstring for the full
explanation.

`dashboard/data.py` has no Streamlit dependency at all, specifically so it's
testable (`tests/test_dashboard_data.py`) without the optional `dashboard`
dependency group installed, and so it's ready to back a future API or a
different UI framework without any changes.

## Known limitations (this phase)

- No auto-refresh / live-updating charts yet — Streamlit re-runs on
  interaction, not on a timer. Live monitoring (auto-refresh, streaming
  updates) is intentionally left for a future phase per the modular
  `views/` structure.
- Date-range and side filtering on the Trading History page are applied in
  Python after fetching from `Database` (which doesn't natively support those
  filters), not pushed down to SQL — fine at current data volumes, worth
  revisiting if trade history grows very large.
