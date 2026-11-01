# Hyper-Engine

## Algorithmic Trading Infrastructure by RookLab

> A modular Python framework for quantitative strategy research, backtesting, risk management, and testnet execution on Hyperliquid. Developed under **RookLab**, Hyper-Engine brings trading logic, execution-state tracking, persistence, and monitoring together in one system.

**Project:** Hyper-Engine
**Organization:** RookLab
**Category:** Crypto Algorithmic Trading Infrastructure
**Primary Language:** Python
**Exchange Integration:** Hyperliquid
**Execution Environment:** Paper trading and explicitly gated testnet execution
**Monitoring:** FastAPI, React + TypeScript, and Streamlit
**Persistence:** SQLite

---

## 1. Project Overview

Hyper-Engine is an algorithmic trading framework developed under RookLab for the Hyperliquid ecosystem.

The goal was to build more than an isolated trading bot or a single strategy. The project explores the engineering systems required to take a trading idea from historical data and quantitative evaluation through risk checks, execution, persistence, and monitoring.

The framework brings together:

* Market data retrieval and validation
* Technical indicator implementation
* Strategy development and comparison
* Historical backtesting
* Out-of-sample and walk-forward analysis
* Monte Carlo analysis
* Position sizing and risk controls
* Hyperliquid API integration
* Order and fill state management
* SQLite persistence
* A read-only API and monitoring interfaces
* Automated testing and code-quality checks

The emphasis is on building a modular system in which the research, execution, persistence, and monitoring components have distinct responsibilities.

Hyper-Engine is an evolving engineering and research project. Its architecture and automated tests are intended to support further development, but they do not establish profitability or production/mainnet readiness.

---

## 2. The Engineering Problem

A basic trading bot is often described as:

```text
Market Data → Strategy → Buy / Sell
```

That description leaves out much of the engineering required to operate a trading system reliably.

A more complete system needs to answer questions such as:

* Is the historical data valid and suitable for analysis?
* How should strategies be evaluated consistently?
* Are reported performance metrics meaningful, or are they artifacts of a small sample?
* Does a trading signal satisfy the relevant risk checks?
* Was an order actually submitted?
* Was the order accepted, partially filled, completely filled, rejected, or left in an uncertain state?
* What happens when an API request times out?
* Can trading activity be reconstructed after a process restart?
* Can an operator inspect the persisted state without modifying execution?
* Can important failure scenarios be tested without submitting real orders?

Hyper-Engine was built to explore these concerns as parts of one integrated framework instead of treating them as unrelated scripts.

The central engineering principle is that **a strategy signal, an order request, an exchange response, a fill, and a completed trade are different events**. The system must not treat them as interchangeable.

---

## 3. System Architecture

Hyper-Engine separates its major responsibilities into modular components.

```text
                       ROOKLAB
                          │
                     HYPER-ENGINE
                          │
             ┌────────────┴────────────┐
             │                         │
        Market Data              Strategy Layer
             │                         │
             └────────────┬────────────┘
                          │
                     Backtesting
                          │
                 Performance Analysis
                          │
                   Strategy Selection
                          │
                    Risk Management
                          │
                  Execution Workflows
                          │
                 Orders / Fills / State
                          │
                    Trade Logging
                          │
                       SQLite
                          │
             ┌────────────┴────────────┐
             │                         │
        FastAPI Read API         Streamlit Data Layer
             │                         │
      React + TypeScript          Streamlit UI
             │                         │
             └────────────┬────────────┘
                          │
                  Read-only Monitoring
```

This diagram represents the system's major responsibilities rather than a claim that every operation runs in one fully automated production pipeline.

The architecture is designed to keep strategy research, risk management, execution, persistence, and monitoring separate. This makes individual components easier to test and allows the system to evolve without requiring a complete rewrite.

---

## 4. Market Data

Hyper-Engine integrates with Hyperliquid's API to retrieve market data for quantitative research and trading workflows.

Historical OHLCV candles provide the input for technical indicators and backtesting. Data validation and cleaning help ensure that the downstream components operate on data that meets the framework's expected requirements.

A typical research workflow includes:

```text
Connect to configured Hyperliquid environment
                    ↓
Retrieve historical candles
                    ↓
Validate and clean the data
                    ↓
Calculate indicators
                    ↓
Evaluate strategy signals
                    ↓
Run backtests
```

An illustrative run might report:

```text
Fetching 500 candles of BTC 1h...
Fetched 500 candles
Data cleaned: 500 kept, 0 removed
```

This is an example of expected program output, not a claim that every run retrieves the same number of candles or produces the same result.

The framework also includes WebSocket-related functionality for market-data and execution workflows. Historical data retrieval, live data handling, and order execution have different operational requirements and should be evaluated separately.

---

## 5. Technical Indicators

The core technical indicators are implemented directly in Python rather than relying on a dedicated technical-analysis library for their calculations.

The indicator set includes:

| Indicator                            | Purpose                                                          |
| ------------------------------------ | ---------------------------------------------------------------- |
| Exponential Moving Average (EMA)     | Identify changes in trend and momentum                           |
| Relative Strength Index (RSI)        | Measure recent price momentum                                    |
| Bollinger Bands                      | Evaluate price relative to a moving average and volatility bands |
| Volume Weighted Average Price (VWAP) | Compare price with a volume-weighted reference                   |

The RSI implementation uses Wilder smoothing.

Implementing the calculations directly provides more control over their behavior and makes it possible to test the indicator layer independently.

It also makes numerical assumptions, initialization behavior, and edge cases easier to inspect than when calculations are hidden behind a third-party abstraction.

---

## 6. Strategy Layer

Hyper-Engine supports multiple strategies through a shared strategy and backtesting framework.

The current strategy family includes:

* EMA Crossover
* RSI
* Bollinger Bands
* VWAP Crossover

The strategies use different approaches to interpreting price behavior. For example, an EMA crossover looks for changes in the relationship between faster and slower moving averages, while RSI and Bollinger-based strategies evaluate momentum or price relative to their respective thresholds and bands.

Using a common backtesting interface allows the strategies to be compared under consistent assumptions.

This does not imply that the strategies are profitable or suitable for every market regime. Their value must be evaluated using sufficient data, realistic execution assumptions, transaction costs, and out-of-sample testing.

---

## 7. Backtesting and Strategy Evaluation

The backtesting engine evaluates strategy behavior using historical market data.

The framework records performance information such as:

* Trade count
* Profit and loss
* Win rate
* Returns
* Sharpe ratio
* Drawdown-related metrics

The strategy-selection workflow uses Sharpe ratio to rank candidate strategies, subject to the configured selection requirements.

A high Sharpe ratio is not sufficient evidence of a robust strategy. Small samples, data leakage, unrealistic fills, parameter selection, and missing transaction costs can all make historical results appear stronger than they are.

### Interpreting historical results

An earlier exploratory run produced the following output:

| Strategy        | Trades |    PnL |  Sharpe | Win rate |
| --------------- | -----: | -----: | ------: | -------: |
| VWAP Crossover  |      3 | +14.53 |  9.3583 |    66.7% |
| Bollinger Bands |      6 |  +2.55 |  7.5927 |    66.7% |
| EMA Crossover   |     10 |  +7.86 |  2.9529 |    20.0% |
| RSI             |      2 |  -0.10 | -1.7589 |    50.0% |

**These figures are a historical exploratory example, not a current benchmark or a claim of profitability.** They describe a small number of trades and are insufficient to establish statistical reliability. The figures should only be retained in a public release if their source run and calculation assumptions can be verified.

In particular, a strategy producing three trades cannot be considered robust merely because its reported Sharpe ratio is high.

A more convincing evaluation requires multiple market conditions, sufficiently large samples, realistic trading costs, out-of-sample results, and evidence that the strategy does not depend on a narrow set of historical observations.

---

## 8. Quantitative Validation

Hyper-Engine includes additional research tools intended to examine strategy behavior beyond a single historical backtest.

### Out-of-sample analysis

Out-of-sample evaluation examines performance on data that was not used for the initial strategy selection or parameter decisions.

Its usefulness depends on maintaining a genuine separation between the research data and the evaluation data.

### Walk-forward analysis

Walk-forward analysis evaluates a strategy across successive periods. It can help reveal whether performance remains reasonably stable as market conditions change.

### Monte Carlo analysis

Monte Carlo analysis examines a range of possible outcomes under the assumptions of the simulation.

It can help characterize uncertainty and downside scenarios, but it cannot guarantee future performance or compensate for unrealistic assumptions in the underlying backtest.

These tools are part of the framework's research capabilities. Their existence should not be interpreted as proof that every strategy has passed a comprehensive validation process.

---

## 9. Risk Management

Hyper-Engine separates risk management from individual strategy implementations.

The framework includes functionality for areas such as:

* Position sizing
* Risk limits
* Daily loss controls
* Kelly-based sizing
* Position constraints
* Execution safety checks

This separation is important because a strategy should express a trading decision without independently deciding whether that decision is permitted to reach the exchange.

A typical conceptual workflow is:

```text
Strategy Signal
      ↓
Risk Evaluation
      ↓
Execution Eligibility
      ↓
Order Workflow
```

The exact enforcement behavior depends on the relevant execution path, configuration, and persisted risk state.

A configured risk limit is not a guarantee that every failure scenario is handled correctly. Enforcement needs to be verified through implementation review and tests covering boundary conditions, concurrent activity, process restarts, and uncertain exchange responses.

The monitoring dashboard also distinguishes displayed information from authoritative enforcement state. For example, a loss estimate calculated from persisted trades should not be treated as the authoritative daily-loss counter used by the execution safeguards.

---

## 10. Hyperliquid Integration and Execution

Hyper-Engine integrates with Hyperliquid for API communication and order-execution workflows.

The integration includes functionality related to:

* API client operations
* Account authentication
* EIP-712 signing
* Order submission
* Order and fill tracking
* Position-related operations
* Order cancellation
* Testnet execution

The framework distinguishes between paper trading and real order submission on Hyperliquid testnet.

Paper trading is intended for evaluating the trading workflow without submitting real exchange orders. Testnet execution submits actual requests to the exchange's test environment and therefore requires additional care even though it does not expose the same real-capital risk as mainnet execution.

Testnet execution is protected by explicit configuration and safety checks intended to prevent accidental mainnet execution.

The existence of exchange integration does not, by itself, establish that the framework is ready for unattended production deployment.

---

## 11. Execution-State Management

One of the key engineering concerns is keeping local execution state aligned with what is known about exchange activity.

A signal indicating `BUY` does not mean that a buy order was filled. Likewise, a successful API request does not necessarily mean the intended trade has been completed.

A simplified two-leg lifecycle is:

```text
BUY requested
      ↓
BUY submitted
      ↓
BUY fill confirmed
      ↓
SELL becomes eligible
      ↓
SELL submitted
      ↓
SELL fill confirmed
      ↓
Trade completion recorded
```

The important distinction is between an order request and the resulting exchange state.

Failures, timeouts, and uncertain responses must not silently advance the trading lifecycle as though execution had succeeded. Depending on the failure, the system may need to reconcile local state with exchange state before allowing further activity.

This design focus helps reduce the risk of treating unconfirmed orders as completed trades.

However, no local state machine can eliminate every exchange, networking, or process failure. Execution reconciliation and recovery behavior require independent verification.

---

## 12. Persistence and Recovery

Hyper-Engine uses SQLite to persist trading and analysis information.

The persistence layer supports records associated with:

* Trading sessions
* Signals
* Orders
* Fills
* Trades
* Execution-related events
* Backtest and performance results
* Out-of-sample and walk-forward analysis
* Monte Carlo analysis

Persistence provides a record of activity beyond terminal output or in-memory state.

It also allows monitoring interfaces to inspect historical activity without depending on the continued existence of the original trading process.

Database reliability involves more than successfully inserting rows. Schema management, interrupted writes, state consistency, recovery, and compatibility across versions also matter.

Hyper-Engine includes database migration and recovery-related work intended to improve the reliability of persisted state. The degree of recovery supported by a given version should be determined from the implementation and its tests, not assumed from the presence of a database.

SQLite is appropriate for the current modular framework, while more demanding multi-process or distributed deployment requirements would need separate evaluation.

---

## 13. Monitoring Interfaces

Hyper-Engine provides two read-only monitoring interfaces.

### React + TypeScript Web Dashboard

The web dashboard is a browser-based application built with React, TypeScript, and Vite.

It communicates with the FastAPI read API and provides views for:

* Overview
* Sessions
* Trades
* Performance
* Strategy analysis
* Monte Carlo analysis
* Risk information

The frontend uses polling rather than WebSockets for data freshness in its current implementation.

The FastAPI backend exposes read-oriented endpoints for accessing persisted system information. This separates the web presentation layer from the trading and database internals.

The dashboard is intended for observation and analysis, not execution control.

### Streamlit Dashboard

The existing Streamlit interface provides access to persisted:

* Sessions and trading history
* Orders and fills
* Performance metrics
* Strategy-analysis results
* Monte Carlo results
* Risk information

It uses the existing database access layer rather than creating an independent trading system.

### Read-only design

Both interfaces are intended to inspect system state without providing controls for placing, cancelling, or modifying orders.

This separation reduces the coupling between monitoring and execution and makes the interfaces useful for reviewing activity and debugging system behavior.

A read-only interface does not, by itself, guarantee that the underlying trading process is healthy. For example, a session whose `ended_at` field is `null` is not proof that a trading process is still running.

---

## 14. Testing and Code Quality

Automated testing is an important part of Hyper-Engine's development process.

The test suite covers multiple areas of the framework, including:

* Technical indicators
* Strategy behavior
* Backtesting
* Performance calculations
* Risk management
* Execution behavior
* Order and fill handling
* Database persistence
* API behavior
* Dashboard data access
* Error and recovery scenarios

External exchange interactions are mocked where appropriate so tests can examine expected behavior without submitting real exchange orders.

The project also uses tools such as Pytest, Ruff, and Mypy to support testing, code quality, and static type checking.

Typical validation commands include:

```bash
poetry run pytest
poetry run ruff format --check .
poetry run ruff check .
poetry run mypy src tests
```

The web dashboard has its own validation commands, run from the `web/` directory:

```bash
npm run typecheck
npm run lint
npm run test
npm run build
```

Test results should be reported with the command and date of the run. A passing test suite provides evidence for the scenarios it covers; it does not prove profitability, eliminate every possible execution failure, or establish mainnet readiness.

---

## 15. Technology Stack

### Core Engineering

* Python
* Poetry
* Pydantic
* Async programming

### Exchange Integration

* Hyperliquid APIs
* HTTP clients
* WebSocket-related functionality
* EIP-712 signing
* `httpx`
* `asyncio`

### Quantitative Research

* NumPy
* Pandas
* Custom technical indicators
* Backtesting engine
* Performance analysis
* Out-of-sample analysis
* Walk-forward analysis
* Monte Carlo analysis

### Persistence

* SQLite
* Database schema and migration functionality

### API

* FastAPI
* Typed response schemas
* Read-oriented HTTP endpoints

### Web Dashboard

* React
* TypeScript
* Vite
* TanStack Query
* Vitest

### Python Monitoring

* Streamlit

### Testing and Code Quality

* Pytest
* Ruff
* Mypy
* Frontend testing and linting tools

---

## 16. Engineering Principles

Several principles guide Hyper-Engine's design.

### Explicit state

Order requests, submissions, fills, failures, timeouts, and completed trades must be treated as distinct states.

### Separation of concerns

Strategy logic should not be responsible for exchange authentication, database implementation, risk enforcement, or dashboard rendering.

### Risk before execution

Trading decisions should pass through the required risk and execution checks before they are allowed to submit orders.

### Persistent records

Trading activity and analysis results should be available beyond the lifetime of an individual process.

### Observable behavior

Monitoring interfaces should make persisted activity easier to inspect without coupling the presentation layer to trading execution.

### Testable components

Individual components and critical interactions should be testable without requiring real exchange orders.

### Evidence over assumptions

Backtest metrics, passing tests, and successful testnet requests each provide different types of evidence. None should be used as a substitute for the others.

---

## 17. Development Workflow

Hyper-Engine follows a development approach that separates research from execution.

```text
Strategy Development
        ↓
Historical Backtesting
        ↓
Performance Evaluation
        ↓
Out-of-sample / Walk-forward Analysis
        ↓
Risk and Execution Validation
        ↓
Paper Trading
        ↓
Testnet Execution
        ↓
Further Reliability Evaluation
```

This workflow is a development model, not a claim that every strategy or release has completed every stage.

Progressing from testnet to mainnet would require a separate readiness assessment, including execution reconciliation, failure recovery, operational monitoring, security, risk enforcement, and realistic performance validation.

---

## 18. Future Development

Potential future directions for Hyper-Engine under RookLab include:

* More advanced execution algorithms
* Additional exchange integrations
* More extensive market-microstructure research
* Improved execution-cost and slippage models
* More comprehensive portfolio-level risk management
* Stronger automated research and evaluation workflows
* More advanced dashboard analytics
* WebSocket-driven monitoring where justified
* Performance-critical components implemented in Rust, if profiling supports the need
* Deployment and operational tooling for more demanding environments

These are potential development directions, not claims about existing functionality.

The priority is to improve the framework's correctness, research quality, execution reliability, and observability before adding complexity that is not justified by measurable requirements.

---

## 19. What Hyper-Engine Demonstrates

Hyper-Engine is an engineering project focused on the systems surrounding algorithmic trading.

The work demonstrates practical exploration of:

* Modular Python architecture
* Financial API integration
* Asynchronous programming
* Technical indicator implementation
* Strategy design
* Backtesting and quantitative performance analysis
* Risk-management architecture
* Exchange order and fill workflows
* EIP-712 signing
* Persistent trading state
* Database migration and recovery concerns
* Read-only APIs
* Monitoring dashboards
* Automated testing and static analysis

The most important lesson is that building a trading system involves more than generating buy and sell signals. It requires careful treatment of state, uncertainty, persistence, risk, and the difference between what a strategy intended to happen and what the exchange actually confirmed.

Hyper-Engine serves as the foundation for continued algorithmic trading infrastructure development within RookLab.

---

## 20. Disclaimer

Hyper-Engine is a software engineering and quantitative research project.

Backtested results are historical observations and do not guarantee future performance or live profitability. Testnet execution validates aspects of exchange interaction in a simulated environment but does not establish production reliability or guarantee equivalent mainnet behavior.

The framework should not be considered production-ready solely because its components exist or its automated tests pass.

This project is not financial advice. Anyone evaluating the software for use with real capital should independently assess its implementation, security, operational reliability, and risk controls.

---

## About RookLab

**RookLab** is the project organization behind Hyper-Engine, focused on crypto algorithmic trading infrastructure and software for quantitative research, strategy evaluation, execution, and monitoring.

Hyper-Engine is one part of that broader engineering direction.

### Project Links

* **RookLab GitHub:** https://github.com/RookLab-dev
* **Hyper-Engine repository:** https://github.com/RookLab-dev/Hyper-Engine-
* **RookLab website:** https://rooklab.online
* **RookLab on X:** https://x.com/Rook_Lab_
* **Developer LinkedIn:** https://www.linkedin.com/in/daniel-j-2b4a413a7/
