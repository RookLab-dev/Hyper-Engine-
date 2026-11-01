# Hyper-Engine

A production-grade algorithmic trading framework built on the [Hyperliquid](https://hyperliquid.xyz) L1 blockchain.

Built from scratch in Python — no trading libraries, no shortcuts. Every indicator, strategy, and risk rule is implemented from the mathematical formula up.

---

## What It Does

```
Fetch real BTC data from Hyperliquid
    → Clean the data
        → Backtest EMA, RSI, Bollinger Bands, and VWAP strategies
            → Pick the best by Sharpe ratio
                → Paper trade the winner with live market data
                  (or, with ENABLE_TESTNET_LIVE_EXECUTION=true,
                   place REAL orders on Hyperliquid TESTNET instead)
```

Every session, order, fill, and completed trade — paper or real testnet —
is persisted to a local SQLite database and browsable in a read-only dashboard. See [Dashboard](#dashboard) below.

---

## Architecture

```
src/
├── hyperliquid/     ← Hyperliquid API client (auth, HTTP, responses, trading)
├── data/            ← OHLCV candle fetching and pagination
├── indicators/      ← EMA, RSI, Bollinger Bands, VWAP (built from formula)
├── strategies/      ← EMA crossover, RSI, Bollinger, VWAP (signal generation)
├── backtester/      ← Backtest engine + performance/OOS/walk-forward/Monte Carlo
├── risk/            ← Position sizing, daily loss limits, Kelly criterion
├── execution/       ← REAL Hyperliquid TESTNET order execution (explicitly gated)
├── core/            ← Config, logging, exceptions, utilities, SQLite persistence
├── dashboard/       ← Read-only monitoring dashboard
└── strategy_runner.py ← Main pipeline (paper trade OR real testnet execution)
```

---

## Installation

**Requirements:** Python 3.12+, [Poetry](https://python-poetry.org)

```bash
git clone https://github.com/Rook0z/Hyper-Engine-.git
cd Hyper-Engine-
poetry install
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
HL_PRIVATE_KEY=0x...        # API wallet private key
HL_ACCOUNT_ADDRESS=0x...    # Master wallet address (NOT the API wallet)
HL_BASE_URL=https://api.hyperliquid-testnet.xyz
```

> **Always use testnet first.** Never set `IS_MAINNET=true` until fully verified.

All other settings have sensible defaults. Override in `.env` only if needed:

```env
SYMBOL=BTC
INTERVAL=1h
POSITION_SIZE=0.001
INITIAL_CAPITAL=10000
RUN_HOURS=2
```

---

## Usage

### Run the full pipeline (paper trading — default)

```bash
poetry run python -m strategy_runner
```

Output:
```
=================================================================
  HYPER-ENGINE STRATEGY PIPELINE
=================================================================

Fetching 500 candles of BTC 1h...
Backtesting EMA Crossover 9/21...
Backtesting RSI(14) OB=70/OS=30...
Backtesting Bollinger(20, 2.0)...
Backtesting VWAP...

=================================================================
  STRATEGY COMPARISON
=================================================================
  Strategy                       Trades        PnL   Sharpe  WinRate
-----------------------------------------------------------------
  RSI(14) OB=70/OS=30                 2      +0.11   0.7606   50.0%
  Bollinger(20, 2.0)                  5      -3.55  -5.7327   40.0%
  EMA Crossover 9/21                 13      -4.32  -6.8681   38.5%
=================================================================

  Selected : RSI(14) OB=70/OS=30
  Starting 2h paper trade...
```

### Run in REAL testnet execution mode

Set, in `.env`:
```env
IS_MAINNET=false
ENABLE_TESTNET_LIVE_EXECUTION=true
```
then run the exact same command:
```bash
poetry run python -m strategy_runner
```
`run_pipeline()` automatically routes to `live_testnet_trade()` instead of
`paper_trade()` — same backtest/selection stage, but every BUY/SELL the
strategy signals becomes a REAL order on Hyperliquid TESTNET (via
`execution/testnet_executor.py`), submitted only after a risk check, and
only ever advancing to SELL once the BUY is CONFIRMED filled. The process
keeps running and looking for further signals for `RUN_HOURS` — it does
not exit after one trade. Every order/fill/trade/session is persisted to
SQLite and viewable in the [dashboard](#dashboard).

To prove the execution path works end-to-end without waiting on a live
signal, run the smoke test — it places one real BUY and one real SELL
immediately and persists them the same way:
```bash
poetry run python -m execution.smoke_test
```

> **Always testnet first.** `ENABLE_TESTNET_LIVE_EXECUTION` and
> `IS_MAINNET` are independent, explicit safety switches —
> `TestnetExecutor` refuses to construct unless both are correctly set.

### Run tests

```bash
poetry run pytest --cov=src
```

### Lint and format

```bash
poetry run ruff format .
poetry run ruff check .
poetry run mypy src tests
```

---

## Dashboards

Hyper-Engine provides two read-only interfaces for monitoring persisted trading activity and analysis results.

### Web Dashboard — React + TypeScript

The web dashboard is a React + TypeScript application backed by the FastAPI read API. It provides Overview, Sessions, Trades, Performance, Analysis, Monte Carlo, and Risk views.

The dashboard is for monitoring and reviewing persisted data. It does not provide trading controls.

Start the API from the repository root:

```bash
poetry run uvicorn api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

In a second terminal, start the frontend:

```bash
cd web
npm install
npm run dev
```

Open the local address printed by Vite in your terminal. The development server proxies `/api` requests to the local FastAPI backend.

See [`web/README.md`](web/README.md) for frontend commands and [`docs/dashboard.md`](docs/dashboard.md) for dashboard architecture and data sources.

### Streamlit Dashboard

The existing Streamlit dashboard remains available for browsing persisted sessions, trades, orders and fills, performance metrics, and stored backtest, out-of-sample, walk-forward, and Monte Carlo analysis results.

It is read-only and does not place, cancel, or modify orders.

```bash
poetry install --with dashboard
poetry run streamlit run src/dashboard/app.py
```

See [Dashboards](#dashboards) below. for details about the persistence layer and dashboard limitations.


---

## Strategies

| Strategy | Type | Signal |
|---|---|---|
| EMA Crossover | Trend following | Fast EMA crosses above/below slow EMA |
| RSI | Mean reversion | RSI crosses below 30 (buy) or above 70 (sell) |
| Bollinger Bands | Mean reversion | Price crosses outside the bands |
| VWAP | Trend or mean reversion | Price crosses VWAP (crossover mode) or reverts from VWAP bands (reversion mode) |

The pipeline backtests all four on recent data and selects the best by Sharpe ratio. A minimum Sharpe of 0.5 is required to proceed to paper/live trading.

---

## Adding a Strategy

1. Create `src/strategies/my_strategy.py`
2. Inherit from `BaseStrategy`
3. Implement `generate_signal(closes: list[float]) -> str`
4. Return `"BUY"`, `"SELL"`, or `"HOLD"` — nothing else
5. Write tests in `tests/test_my_strategy.py`
6. Add to the strategies list in `strategy_runner.py`

```python
from strategies.base_strategy import BaseStrategy

class MyStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "My Strategy"

    @property
    def min_periods(self) -> int:
        return 20  # minimum closes needed

    def generate_signal(self, closes: list[float]) -> str:
        if len(closes) < self.min_periods:
            return self.HOLD
        # your logic here
        return self.HOLD
```

---

## Project Structure

```
Hyper-Engine/
├── src/
│   ├── hyperliquid/
│   │   ├── auth.py           ← EIP-712 signing, phantom agent
│   │   ├── client.py         ← HTTP client, retry + backoff
│   │   ├── responses.py      ← Pydantic response models
│   │   ├── symbol.py         ← Asset ID resolution
│   │   ├── market.py         ← Prices, orderbook, trades
│   │   ├── account.py        ← Balances, positions, fills
│   │   └── trading.py        ← Orders, cancels, leverage
│   ├── data/
│   │   └── ohlcv_provider.py ← Candle fetching + pagination
│   ├── indicators/
│   │   ├── ema.py            ← Exponential Moving Average
│   │   ├── rsi.py            ← RSI (Wilder smoothing)
│   │   ├── bollinger.py      ← Bollinger Bands
│   │   └── vwap.py           ← Volume-Weighted Average Price
│   ├── strategies/
│   │   ├── base_strategy.py  ← Abstract base class
│   │   ├── ema_strategy.py   ← EMA crossover
│   │   ├── rsi_strategy.py   ← RSI overbought/oversold
│   │   ├── bb_strategy.py    ← Bollinger mean reversion
│   │   └── vwap_strategy.py  ← VWAP crossover / reversion
│   ├── backtester/
│   │   ├── backtester.py     ← Backtest engine (pandas)
│   │   ├── performance.py    ← Sharpe, Sortino, Calmar, drawdown, etc.
│   │   ├── out_of_sample.py  ← In-sample / out-of-sample split testing
│   │   ├── walk_forward.py   ← Rolling train/test window testing
│   │   └── monte_carlo.py    ← Trade-sequence robustness simulation
│   ├── stats/
│   │   └── basic_stats.py    ← Shared statistical helpers
│   ├── risk/
│   │   └── risk_manager.py   ← Position sizing, Kelly criterion
│   ├── execution/
│   │   ├── testnet_executor.py ← REAL Hyperliquid TESTNET order execution
│   │                          (safety-gated: IS_MAINNET=false AND
│   │                          ENABLE_TESTNET_LIVE_EXECUTION=true required)
│   │   └── smoke_test.py     ← One-shot BUY→fill→SELL→fill proof, persisted
│   ├── core/
│   │   ├── config.py         ← Centralised settings (pydantic-settings)
│   │   ├── database.py       ← SQLite persistence (sessions/orders/fills/
│   │                          trades/analysis_results) — the dashboard's
│   │                          only data source
│   │   ├── trade_logger.py   ← Structured JSONL trade logging + optional
│   │                          Database persistence (paper_trade(),
│   │                          live_testnet_trade(), and smoke_test.py all
│   │                          pass db=Database() so every run shows up in
│   │                          the dashboard)
│   │   ├── exceptions.py     ← Custom exception hierarchy
│   │   └── utils.py          ← Shared utilities
│   ├── dashboard/
│   │   ├── app.py            ← Streamlit entrypoint
│   │   ├── data.py           ← Read-only data access layer (no UI imports)
│   │   └── views/            ← Overview, Trading History, Orders & Fills,
│   │                          Performance, Strategy Analysis, Monte Carlo, Risk
│   └── strategy_runner.py    ← Main pipeline — paper_trade() by default,
│                              live_testnet_trade() when
│                              ENABLE_TESTNET_LIVE_EXECUTION=true
├── tests/                    ← Full test suite (200+ tests)
├── docs/
│   ├── Hyper-Engine_architecture.md
│   ├── dashboard.md
│   └── hyperliquid_notes.md
├── storage/                  ← hyper_engine.db (SQLite, gitignored)
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Key Technical Decisions

- **EIP-712 signing** — Hyperliquid uses Ethereum wallet auth, not API keys. Every order is signed with a private key using the phantom agent construction.
- **No trading libraries** — every indicator is built from the mathematical formula. EMA, RSI (Wilder smoothing), Bollinger Bands, VWAP — all from scratch.
- **Pandas + numpy backtester** — vectorised operations, no Python loops for performance-critical code.
- **TDD** — tests written alongside the code. 200+ tests, all HTTP mocked, zero real network calls in the test suite.
- **Pydantic v2 responses** — every API response validated at the boundary with `extra="ignore"` for forward compatibility.
- **Centralised config** — all settings in one place via `pydantic-settings`. Override anything from `.env`.
- **SQLite as the single source of truth for the dashboard** — `paper_trade()`, `live_testnet_trade()`, and `execution/smoke_test.py` all write through the same `TradeLogger(db=Database())`, so the dashboard never has a second, divergent logging path to keep in sync.
- **Explicit, independent safety switches for real execution** — `IS_MAINNET` and `ENABLE_TESTNET_LIVE_EXECUTION` are separate flags; `TestnetExecutor` re-verifies both itself at construction time rather than trusting any caller to have checked first.

---

## Logs

Every paper trade / live testnet execution / smoke test session writes
structured JSONL logs to `logs/trades_YYYY-MM-DD.jsonl` **and** the same
session/order/fill/trade rows to `storage/hyper_engine.db` (SQLite) — see
[Dashboard](#dashboard) for browsing the latter.

```bash
# View today's signals
cat logs/trades-$(date +%Y-%m-%d).jsonl | python -m json.tool

# Count trades
grep '"action": "TRADE_CLOSED"' logs/trades-*.jsonl | wc -l
```

---


## License

MIT