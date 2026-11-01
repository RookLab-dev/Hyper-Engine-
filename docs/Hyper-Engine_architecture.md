# Hyper-Engine Architecture

## What Hyper-Engine Is

Hyper-Engine is a Python-based, production-oriented algorithmic trading framework developed under **RookLab** for the Hyperliquid L1 ecosystem.

The framework covers the trading lifecycle, from exchange connectivity and market data to strategy research, backtesting, risk management, paper trading, testnet execution, persistence, and monitoring.

Its architecture includes a Python trading engine, a FastAPI read-only API, a React + TypeScript web dashboard, and the existing Streamlit dashboard.

The project prioritizes modularity, measurable strategy performance, risk controls, testable components, and safeguards against unintended order execution. It is a development and research project, not a claim of profitability or verified mainnet readiness.

---

## Full Project Structure

The following is a high-level view of the repository. Individual files and optional components may change as development progresses.

```text
Hyper-Engine/
├── docs/
│   ├── Hyper-Engine_architecture.md
│   ├── dashboard.md
│   ├── database.md
│   └── hyperliquid_notes.md
│
├── src/
│   ├── api/                 # FastAPI read-only API
│   ├── hyperliquid/         # Exchange connectivity and client
│   ├── stats/               # Statistical utilities
│   ├── data/                # Market data providers
│   ├── indicators/          # Technical indicators
│   ├── strategies/          # Strategy implementations
│   ├── backtester/          # Historical simulation and analysis
│   ├── risk/                # Risk management
│   ├── execution/           # Testnet execution and smoke tests
│   ├── core/                # Configuration, persistence, shared logic
│   └── dashboard/           # Existing Streamlit dashboard
│
├── web/                     # React + TypeScript web dashboard
├── tests/                   # Automated tests and fixtures
├── storage/                 # Local SQLite persistence; gitignored
├── pyproject.toml
├── poetry.lock
├── .env.example
├── .gitignore
├── README.md
└── CASE_STUDY.md
```

Private development instructions and local environment files are intentionally excluded from this public-facing structure.

---

## Hyperliquid API Client

**Location:** `src/hyperliquid/`

This package contains the exchange-integration components used to communicate with Hyperliquid.

The package includes functionality for:

* Authentication and request signing.
* HTTP communication with Hyperliquid API endpoints.
* Exchange response handling and validation.
* Asset and symbol identification.
* Market data retrieval.
* Account and position information.
* Order submission and cancellation through the exchange integration.

The precise responsibilities of individual modules are defined by their current implementations.

### Design principles

* Keep exchange-specific behavior within the exchange integration layer where practical.
* Separate market-data retrieval from strategy decisions.
* Keep authentication and signing logic distinct from strategy logic.
* Validate responses and handle exchange errors explicitly.
* Avoid creating independent order-submission paths that bypass execution safeguards.

The WebSocket client and live-streaming capabilities should be documented according to their actual implementation status. A planned or partially implemented streaming feature must not be presented as a completed production capability.

---

## Execution Engine and Strategy

### `src/stats/` — Statistical Utilities

This package contains statistical calculations used by strategy research and performance analysis.

Examples include:

* Mean and standard deviation.
* Variance and expected value.
* Win rate.
* Profit factor.

Statistical calculations should be independently testable and should use clearly defined input assumptions.

These utilities support research and analysis; they do not establish that a strategy is profitable.

### `src/data/` — Market Data

The data layer supplies historical market data to the backtester and other components that require it.

The OHLCV provider retrieves candle data from Hyperliquid and converts the results into a consistent tabular representation for downstream processing.

Data handling should account for:

* Missing or invalid values.
* Timestamp consistency.
* Candle ordering.
* Duplicate observations.
* Exchange pagination limits.
* Data quality checks before backtesting.

Historical data processing and live market-data streaming are distinct responsibilities. Their integration should not be assumed to be identical.

### `src/indicators/` — Technical Indicators

The framework implements technical indicators directly in Python rather than relying on a dedicated technical-analysis library.

The principal indicators include:

* **EMA:** Exponential Moving Average.
* **RSI:** Relative Strength Index, using Wilder smoothing.
* **Bollinger Bands:** Price bands calculated using a moving average and standard deviation.
* **VWAP:** Volume Weighted Average Price.

Indicators transform market data into values that strategies can evaluate.

Where possible, indicator calculations should remain deterministic and independently testable, with explicit handling of insufficient history and invalid inputs.

Indicator values are inputs to strategy decisions, not trading signals by themselves.

### `src/strategies/` — Trading Strategies

The strategy layer translates market data and indicator values into trading signals.

The framework includes strategy implementations based on:

* EMA crossovers.
* RSI momentum conditions.
* Bollinger Band conditions.
* VWAP-based price relationships.

Strategies should follow a consistent interface so that the backtester and strategy runner can work with interchangeable implementations.

A strategy is responsible for determining its signal. It should not independently bypass risk checks or submit exchange orders.

A signal is not an execution confirmation. The risk and execution layers must determine whether a proposed trade is permitted and whether the exchange has actually filled an order.

### `src/backtester/` — Historical Simulation

The backtesting layer evaluates strategy behavior against historical market data.

Its responsibilities include simulating trade decisions, tracking hypothetical positions, applying configured execution assumptions, and calculating performance statistics.

The performance analysis covers measures such as:

* Profit and loss.
* Trade count and win rate.
* Sharpe ratio.
* Sortino ratio.
* Maximum drawdown.
* Equity-curve behavior.

Backtest results depend on the data, strategy parameters, execution assumptions, and evaluation period.

A backtest is not evidence of future profitability.

#### Evaluation and robustness

Strategy evaluation should consider more than headline returns or a single Sharpe ratio.

Useful techniques include:

* Out-of-sample evaluation.
* Walk-forward analysis.
* Monte Carlo analysis.
* Transaction-cost and slippage assumptions.
* Drawdown and downside-risk analysis.
* Sensitivity to market conditions and strategy parameters.

These techniques must be distinguished from fully automated research workflows. The existence of analysis functionality does not necessarily mean that every evaluation method runs automatically during the main trading pipeline.

Particularly high performance on a small sample should be treated cautiously because it can result from noise, overfitting, or unrealistic execution assumptions.

### `src/risk/` — Risk Management

The risk layer evaluates whether a proposed trade satisfies the configured risk constraints.

Depending on the active configuration and execution path, these controls may include:

* Position sizing.
* Position and exposure limits.
* Daily loss limits.
* Drawdown limits.
* Trade eligibility checks.

Position-sizing methods, including Kelly-based sizing, require appropriate assumptions and safeguards. They should not be interpreted as guarantees of optimal sizing or profitability.

The risk manager sits between strategy decisions and trade execution.

A strategy may generate a BUY signal, but that signal must not automatically result in an order. The trade must pass the applicable risk checks and execution safeguards.

Persistent risk state and emergency-stop status are also important to safe operation. Their authoritative enforcement behavior belongs to the trading and risk implementation, not to the monitoring dashboard.

---

## Execution and Trading Safety

### `src/execution/` — Testnet Execution

The execution package contains the components responsible for interacting with the exchange's testnet order-execution path.

The testnet executor is designed to enforce explicit configuration requirements before allowing real testnet order submission.

The documented safeguards include:

* Mainnet mode must be disabled.
* Testnet live execution must be explicitly enabled.
* The configured endpoint must be the testnet endpoint.
* Order-fill status must be verified rather than inferred from an order-submission response.

The exact checks and permitted call paths are defined by the current implementation.

### Confirmed-fill requirement

Order submission and order execution are different events.

A successful submission request does not prove that an order has filled.

Where the execution workflow requires a confirmed BUY before a SELL, the implementation must verify the relevant fill state before submitting the SELL. It must not treat an accepted order response as proof of a completed fill.

The smoke-test workflow exercises the testnet execution path directly and is intended to validate the integration without depending on a strategy producing a signal.

Smoke tests must remain explicitly gated and must never be run against mainnet as part of ordinary documentation or development validation.

### Paper trading versus testnet execution

Hyper-Engine distinguishes between two execution modes:

**Paper trading**

* Simulates trades without submitting real exchange orders.
* Allows strategy and pipeline behavior to be evaluated.
* Depends on the simulator's fill and cost assumptions.

**Testnet execution**

* Submits real orders to the Hyperliquid test environment when explicitly enabled.
* Exposes order-lifecycle behavior that a simulator cannot fully reproduce.
* Can reveal rejection, latency, and fill-handling problems.

Neither mode establishes that a strategy will perform profitably in live markets. Testnet results also do not prove mainnet readiness.

---

## Core Infrastructure and Persistence

### `src/core/` — Shared Infrastructure

The core package contains shared functionality used by the trading engine and its supporting components.

Important responsibilities include:

* Configuration management.
* SQLite persistence.
* Trade logging.
* Shared exceptions and utilities.
* Database schema management and related safeguards.
* Persistent risk-state support where implemented.

Configuration is managed separately from strategy logic. Sensitive credentials and private environment settings must not be published.

### SQLite database

Hyper-Engine uses SQLite for persisted trading and analysis data.

The persistence layer supports records associated with trading sessions, orders, fills, trades, and analysis results, together with other tables required by the current schema.

The database is also the data source for the monitoring interfaces.

The implementation includes schema-version tracking and validation. The database module defines its expected SQLite settings and raises an error when required validation checks fail.

Database schema changes must preserve existing data and follow the project's migration and validation behavior. Database files, backups, and other runtime state do not belong in version control.

See `docs/database.md` for the documented backup and recovery procedures.

### Trade logging

Trade logging provides a record of trading activity and related events.

The framework supports structured JSONL logging and SQLite persistence through the trade-logging and database components.

These records serve different operational purposes:

* Structured logs provide a chronological record that can be inspected independently.
* SQLite makes persisted records available to the application and monitoring interfaces.
* Persistent risk-state records support inspection of relevant state across process lifecycles.

Logging and persistence behavior must be described according to the active call paths. A log message alone is not proof that a corresponding database transaction succeeded.

---

## Monitoring and Dashboard Architecture

Hyper-Engine provides two dashboard interfaces:

1. A React + TypeScript web dashboard backed by a FastAPI read-only API.
2. The existing Streamlit dashboard.

Both are intended for monitoring persisted data and reviewing trading activity. Neither should be treated as a substitute for the trading engine's risk controls.

### `src/api/` — FastAPI Read API

The API exposes read-only endpoints for retrieving application data.

Its responsibilities include serving data for dashboard views such as:

* Overview.
* Trading sessions.
* Trades.
* Performance.
* Strategy analysis.
* Monte Carlo results.
* Risk and emergency-stop status.

The API reads persisted application data through the backend's data-access components.

Its monitoring role is distinct from the execution role. Dashboard requests must not create, cancel, or modify exchange orders.

Risk-related API responses must preserve important distinctions between confirmed status, blocked state, unavailable information, and invalid or unverifiable state. Missing data must not be interpreted automatically as proof that trading is safe.

Some dashboard metrics are derived for display and are not necessarily the authoritative values used by the live risk-enforcement logic.

### `web/` — React + TypeScript Dashboard

The web dashboard is a React and TypeScript application that communicates with the FastAPI backend.

Its principal views cover:

* Overview.
* Sessions.
* Trades.
* Performance.
* Analysis.
* Monte Carlo.
* Risk.

The frontend is responsible for presenting persisted application data and helping the operator inspect trading activity and analysis results.

The dashboard is read-only. It does not provide order-placement, order-cancellation, or trading-control functionality.

The current dashboard uses polling for data freshness rather than assuming that WebSocket-driven updates are available.

An open session record, such as a row with `ended_at: null`, does not by itself prove that a trading process is currently running or healthy.

The frontend and backend have separate responsibilities:

* The API retrieves and serves application data.
* The frontend renders that data.
* The trading engine and risk components remain responsible for trading decisions and enforcement.

See `docs/dashboard.md` and `web/README.md` for the relevant setup and usage instructions.

### `src/dashboard/` — Streamlit Dashboard

The existing Streamlit dashboard remains available alongside the newer web interface.

It provides views for persisted trading activity and analysis results, including relevant historical sessions, trades, orders and fills, performance measures, and stored research outputs.

The Streamlit dashboard is intended for inspection and monitoring, not order execution.

Its data-access layer reads persisted information rather than creating a separate order-submission path.

Keeping the existing dashboard available preserves a familiar interface while the React-based dashboard provides an alternative presentation layer.

See `docs/dashboard.md` for the documented pages, data sources, and limitations.

---

## How the Components Connect

The following diagram illustrates the main conceptual flow. It is not a claim that every research or monitoring operation executes synchronously through a single pipeline.

```text
                  Hyperliquid
                       |
                       v
             src/hyperliquid/
              Exchange integration
                       |
                       v
                 src/data/
                 Market data
                       |
                       v
              src/indicators/
               Indicator values
                       |
                       v
              src/strategies/
                Trade signals
                       |
                       v
                 src/risk/
           Risk and eligibility checks
                       |
                       v
              src/strategy_runner.py
                       |
                +------+------+
                |             |
                v             v
          Paper trading   Testnet execution
          Simulated fills  src/execution/
                |             |
                +------+------+
                       |
                       v
              src/core/trade_logger.py
                       |
                 +-----+-----+
                 |           |
                 v           v
              JSONL       SQLite
                 |           |
                 |           +---------------------+
                 |           |                     |
                 |           v                     v
                 |      src/api/              src/dashboard/
                 |      FastAPI               Streamlit
                 |           |
                 |           v
                 |          web/
                 |      React + TypeScript
                 |
                 v
           Independent log
             inspection
```

### Architectural boundaries

The architecture aims to preserve clear responsibilities:

* **Exchange integration:** Communicates with Hyperliquid.
* **Data:** Retrieves and prepares market data.
* **Indicators:** Calculates market-derived values.
* **Strategies:** Produces trading signals.
* **Backtesting:** Evaluates strategy behavior against historical data.
* **Risk management:** Determines whether proposed trades satisfy applicable constraints.
* **Execution:** Handles the permitted order-execution path.
* **Core infrastructure:** Provides persistence, logging, configuration, and shared functionality.
* **API and dashboards:** Expose and display persisted information for monitoring.

These boundaries help isolate failures and make components easier to test. They do not imply that all components are independent of one another or that every module is completely side-effect-free.

---

## Research, Backtesting, and Execution Are Different Workflows

A strategy's research lifecycle and its execution lifecycle should be kept conceptually separate.

### Research workflow

```text
Historical market data
        |
        v
Data validation and preparation
        |
        v
Indicator calculations
        |
        v
Strategy evaluation
        |
        v
Backtesting and performance metrics
        |
        v
Out-of-sample and robustness analysis
```

The objective is to evaluate strategy behavior and identify weaknesses before relying on a strategy in an execution environment.

### Trading workflow

```text
Market data
     |
     v
Strategy signal
     |
     v
Risk checks
     |
     v
Paper trading OR explicitly enabled testnet execution
     |
     v
Fill verification where required
     |
     v
Logging and persistence
     |
     v
Monitoring interfaces
```

The trading workflow must preserve execution safeguards even when the research results look promising.

Passing a backtest does not authorize unrestricted live execution.

---

## Operational Safety and Limitations

The following principles guide the design and operation of Hyper-Engine.

### Explicit execution gates

Real testnet execution must remain disabled unless the required configuration conditions are satisfied. The executor must independently enforce its required environment checks.

Documentation, tests, and development tooling must not weaken these controls.

### Risk enforcement remains authoritative

Risk enforcement belongs to the trading and risk components. A dashboard or API reporting a particular state must not be mistaken for the mechanism that enforces that state.

If critical risk information is missing, corrupt, or unverifiable, the application must preserve the relevant fail-closed behavior rather than assume a safe state.

### Persisted state requires validation

Database records and log files provide operational evidence, but neither should be treated as infallible.

Recovery procedures must verify the restored database and relevant risk state before the system is relied upon again.

### Backtest results require scrutiny

Backtests can be distorted by unrealistic fills, missing costs, look-ahead bias, overfitting, and inadequate samples.

Performance metrics should be interpreted alongside trade counts, drawdowns, execution assumptions, and out-of-sample results.

### Monitoring is not proof of health

Persisted records can describe past activity without proving that a process is currently running, connected, or able to execute orders safely.

Operational health requires evidence beyond a dashboard row or a successful API response.

### Mainnet readiness is not assumed

The existence of exchange authentication, order-execution code, and testnet integration does not establish production readiness for mainnet trading.

Any future mainnet deployment would require its own explicit authorization, review, testing, operational controls, and deployment procedures.

---

## The Rule That Doesn't Change

Every component should have a clear responsibility.

Strategy logic should not bypass risk management. Monitoring interfaces should not bypass execution safeguards. Persistence should not silently accept invalid state. Research results should not be confused with guarantees of future performance.

The architecture should evolve incrementally, preserving existing behavior and safety controls while making the system easier to test, understand, and operate.

**Hyper-Engine is a RookLab trading-systems engineering and research project. Its goal is to provide a structured foundation for algorithmic trading development—not to promise returns or imply that every planned capability is already complete.**
