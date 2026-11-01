from __future__ import annotations

import logging
import math
import signal
import time
from collections import Counter
from typing import Any
from dotenv import load_dotenv

from core.config import settings
from hyperliquid.auth import HyperliquidAuth
from hyperliquid.client import HyperliquidClient, NetworkError, APIError
from hyperliquid.symbol import HyperliquidSymbol
from data.ohlcv_provider import OHLCVProvider
from strategies.ema_strategy import EMAStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.bb_strategy import BollingerStrategy
from strategies.vwap_strategy import VWAPStrategy
from strategies.base_strategy import BaseStrategy
from strategies.protocols import dispatch_signal
from backtester.backtester import Backtester, BacktestResult
from backtester.costs import CostModel
from backtester.performance import PerformanceAnalyser, PerformanceReport
from backtester.out_of_sample import (
    OutOfSampleReport,
    split_in_out_of_sample,
)
from backtester.walk_forward import (
    WalkForwardReport,
    WalkForwardWindowResult,
    generate_walk_forward_windows,
)
from risk.risk_manager import RiskManager
from core.database import Database
from core.risk_state import RiskGate
from core.trade_logger import TradeLogger
from execution.testnet_executor import (
    TestnetExecutionError,
    TestnetExecutor,
    TestnetSafetyError,
)

load_dotenv()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("strategy_runner")


# ──────────────────────────────────────────────────────────────
# GRACEFUL SHUTDOWN (SIGTERM)
# ──────────────────────────────────────────────────────────────
# Minimal, explicit stop-request flag. request_shutdown() is the only
# writer (called from the SIGTERM handler installed by
# install_shutdown_handlers(), or directly in tests); the trading loops
# read it via shutdown_requested() at the top of each iteration and
# exit through their existing try/finally cleanup — the same path a
# normal RUN_HOURS expiry takes. Never raises, never forces, never
# interrupts an in-flight wait_for_fill poll: a stop arriving mid-tick
# takes effect at the next loop boundary, so an order-status poll runs
# to its normal filled/rejected/timeout outcome and order tracking is
# never silently abandoned.

_shutdown_requested = False


def request_shutdown(*args: Any) -> None:
    """Signals the running loop to stop at the next safe boundary."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.warning("Shutdown requested — finishing the current tick, then stopping.")


def shutdown_requested() -> bool:
    """True once request_shutdown() has run (SIGTERM or test)."""
    return _shutdown_requested


def reset_shutdown_flag() -> None:
    """Clears the stop flag. Tests only — production never un-requests."""
    global _shutdown_requested
    _shutdown_requested = False


def install_shutdown_handlers() -> None:
    """Installs the SIGTERM handler. Called once per process start.

    Safe to call when a handler is already installed (e.g. re-entry in
    tests): keeps the existing behavior by chaining — SIGTERM runs any
    prior handler first, then requests shutdown.
    """
    try:
        previous = signal.getsignal(signal.SIGTERM)
    except (OSError, ValueError, RuntimeError):
        return

    def _on_sigterm(signum: int, frame: Any) -> None:
        if callable(previous) and previous not in (
            signal.SIG_DFL,
            signal.SIG_IGN,
        ):
            try:
                previous(signum, frame)
            except Exception:
                logger.exception("Prior SIGTERM handler raised; continuing shutdown.")
        request_shutdown(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (OSError, ValueError, RuntimeError) as e:
        logger.warning("Could not install SIGTERM handler: %s", e)


install_shutdown_handlers()


# ──────────────────────────────────────────────────────────────
# CONNECT
# ──────────────────────────────────────────────────────────────

# NOTE (P7): the candle-based strategy Protocol used to be duplicated
# here and in backtester.backtester. It now lives in exactly one
# place — strategies.protocols (_CandleBasedStrategy + the shared
# dispatch_signal() helper used at both loop dispatch sites below).


def connect() -> tuple[HyperliquidClient, HyperliquidSymbol, OHLCVProvider]:
    """Connects to Hyperliquid and returns client, symbol map, ohlcv provider."""
    settings.assert_credentials()
    settings.assert_testnet()

    auth = HyperliquidAuth(
        private_key=settings.hl_private_key,
        account_address=settings.hl_account_address,
        is_mainnet=settings.is_mainnet,
    )
    client = HyperliquidClient(
        auth=auth,
        base_url=settings.hl_base_url,
        max_retries=3,
    )
    symbol_map = HyperliquidSymbol(client=client)
    symbol_map.load()
    ohlcv = OHLCVProvider(client=client)

    logger.info("Connected — %s", settings.summary())
    return client, symbol_map, ohlcv


# ──────────────────────────────────────────────────────────────
# FETCH DATA
# ──────────────────────────────────────────────────────────────


def fetch_data(ohlcv: OHLCVProvider) -> list[list]:
    """Fetches OHLCV candles from Hyperliquid."""
    logger.info(
        "Fetching %d candles of %s %s...",
        settings.backtest_candles,
        settings.symbol,
        settings.interval,
    )
    candles = ohlcv.fetch(
        settings.symbol,
        interval=settings.interval,
        limit=settings.backtest_candles,
    )
    logger.info("Fetched %d candles", len(candles))
    return candles


# ──────────────────────────────────────────────────────────────
# CLEAN DATA
# ──────────────────────────────────────────────────────────────


def clean_data(candles: list[list]) -> list[list]:
    """
    Cleans raw OHLCV candles.

    Removes: malformed rows (wrong shape, non-numeric, or non-finite
             NaN/Infinity fields), zero/negative prices, high < low,
             close outside the [low, high] range, duplicate
             timestamps, zero volume.

    Also detects (and logs, but never fills or fabricates) gaps in the
    timestamp sequence, based on the MODAL interval between consecutive
    candles — see _log_timestamp_gaps(). This is purely informational;
    it never changes what's returned.

    A malformed row (e.g. the wrong number of fields, or a field that
    can't be parsed as a number) is skipped and counted, exactly like
    any other invalid candle, rather than raising and aborting the
    whole clean — one bad row from upstream should never take down an
    otherwise-good dataset.
    """
    if not candles:
        raise ValueError("No candles to clean.")

    seen: set[int] = set()
    clean: list[list] = []
    removed = 0
    malformed = 0

    for candle in candles:
        try:
            if len(candle) != 6:
                raise ValueError(f"expected 6 fields, got {len(candle)}")
            ts, open_, high, low, close, volume = candle
            ts = int(ts)
            open_ = float(open_)
            high = float(high)
            low = float(low)
            close = float(close)
            volume = float(volume)
        except (TypeError, ValueError) as e:
            malformed += 1
            removed += 1
            logger.warning("Skipping malformed candle %r: %s", candle, e)
            continue

        if not all(math.isfinite(v) for v in (open_, high, low, close, volume)):
            malformed += 1
            removed += 1
            logger.warning("Skipping non-finite candle %r", candle)
            continue

        if ts in seen:
            removed += 1
            continue
        if any(p <= 0 for p in [open_, high, low, close]):
            removed += 1
            continue
        if high < low:
            removed += 1
            continue
        if not (low <= close <= high):
            removed += 1
            continue
        if volume <= 0:
            removed += 1
            continue

        seen.add(ts)
        clean.append([ts, open_, high, low, close, volume])

    clean.sort(key=lambda c: c[0])

    _log_timestamp_gaps(clean)

    logger.info(
        "Data cleaned: %d kept, %d removed (%d malformed)",
        len(clean),
        removed,
        malformed,
    )
    return clean


def _log_timestamp_gaps(candles: list[list]) -> None:
    """
    Detects gaps in a sorted candle list's timestamp sequence and logs
    a warning summarizing them — never mutates or filters `candles`,
    purely informational.

    The "expected" interval is the MODAL (most common) delta between
    consecutive timestamps, not the minimum or average — robust to a
    handful of genuine gaps skewing those simpler measures. Any delta
    more than 1.5x the modal interval is flagged as a gap; the number
    of likely-missing candles in each gap is estimated as
    round(delta / modal_interval) - 1.
    """
    if len(candles) < 3:
        return

    deltas = [candles[i][0] - candles[i - 1][0] for i in range(1, len(candles))]
    if not deltas:
        return

    modal_delta = Counter(deltas).most_common(1)[0][0]
    if modal_delta <= 0:
        return

    gap_threshold = modal_delta * 1.5
    gaps = [d for d in deltas if d > gap_threshold]

    if gaps:
        missing_estimate = sum(round(d / modal_delta) - 1 for d in gaps)
        logger.warning(
            "Detected %d gap(s) in candle timestamps (expected interval=%dms) "
            "— approximately %d candle(s) may be missing. Largest gap: %dms.",
            len(gaps),
            modal_delta,
            missing_estimate,
            max(gaps),
        )


# ──────────────────────────────────────────────────────────────
# STRATEGY FACTORY
# ──────────────────────────────────────────────────────────────
"""
Every strategy class this runner knows how to build, purely from settings. 
Used both to build the strategies that compete in 
backtest_all() and to build a brand-new instance of the winning
strategy's class for paper trading — see _build_strategy().

"""
STRATEGY_CLASSES: tuple[type[BaseStrategy], ...] = (
    EMAStrategy,
    RSIStrategy,
    BollingerStrategy,
    VWAPStrategy,
)


def _build_strategy(strategy_cls: type[BaseStrategy]) -> BaseStrategy:
    """
    Constructs a brand-new strategy instance of the given class, using
    the current settings — the single source of truth for strategy
    configuration, shared by backtest_all() and paper trading.

    IMPORTANT: this always returns a fresh instance with no prior
    signal-dedup state (_last_signal / _last_crossover start at HOLD).
    The backtester and the paper trader must never share a strategy
    instance — see paper_trade() / run_pipeline() for why.

    Args:
        strategy_cls: One of the classes in STRATEGY_CLASSES.

    Returns:
        A new, unmutated strategy instance.

    Raises:
        ValueError: if strategy_cls is not a known strategy class.
    """
    if strategy_cls is EMAStrategy:
        return EMAStrategy(
            fast_period=settings.ema_fast_period,
            slow_period=settings.ema_slow_period,
        )
    if strategy_cls is RSIStrategy:
        return RSIStrategy(
            period=settings.rsi_period,
            oversold_threshold=settings.rsi_oversold,
            overbought_threshold=settings.rsi_overbought,
        )
    if strategy_cls is BollingerStrategy:
        return BollingerStrategy(
            period=settings.bb_period,
            num_std=settings.bb_num_std,
        )
    if strategy_cls is VWAPStrategy:
        return VWAPStrategy(
            mode=settings.vwap_mode,
            num_std=settings.vwap_num_std,
        )
    raise ValueError(f"Unknown strategy class: {strategy_cls.__name__}")


# ──────────────────────────────────────────────────────────────
# BACKTEST ALL STRATEGIES
# ──────────────────────────────────────────────────────────────


def _default_cost_model() -> CostModel:
    """Builds the run's shared cost model from settings.

    Fees and funding stay at their 0.0 defaults here — only slippage
    comes from settings. Developers inject their own CostModel by
    passing cost_model=... to Backtester or paper_trade() directly.
    """
    return CostModel(slippage_pct=settings.slippage_pct)


def backtest_all(
    candles: list[list],
) -> list[tuple[BaseStrategy, BacktestResult, PerformanceReport]]:
    """Runs all strategies on the same data. Returns sorted by Sharpe desc."""
    strategies: list[BaseStrategy] = [_build_strategy(cls) for cls in STRATEGY_CLASSES]

    analyser = PerformanceAnalyser()
    results = []
    costs = _default_cost_model()

    for strategy in strategies:
        logger.info("Backtesting %s...", strategy.name)
        b = Backtester(
            strategy=strategy,
            initial_capital=settings.initial_capital,
            position_size=settings.position_size,
            slippage_pct=settings.slippage_pct,
            symbol=settings.symbol,
            cost_model=costs,
        )
        result = b.run(candles)
        report = analyser.analyse(result)
        logger.info(
            "  %s → trades=%d PnL=%+.2f sharpe=%.4f winrate=%.1f%%",
            strategy.name,
            report.num_trades,
            report.total_pnl,
            report.sharpe_ratio,
            report.win_rate * 100,
        )
        results.append((strategy, result, report))

    results.sort(key=lambda x: x[2].sharpe_ratio, reverse=True)
    return results


# ──────────────────────────────────────────────────────────────
# OUT-OF-SAMPLE TEST

# Strategy SELECTION uses ONLY the in-sample split (via backtest_all(),
# unchanged, called with in_sample candles only). The winning
# strategy's out-of-sample evaluation then uses a completely FRESH
# instance (via _build_strategy(), unchanged) run on the disjoint
# out-of-sample split — never the same mutable object that ran the
# in-sample backtest, for the same reason paper_trade() never reuses
# the backtested instance (see _build_strategy()'s docstring): shared
# instance state would leak information/behavior from one period into
# the other, which is exactly what out-of-sample testing exists to
# prevent.
# ──────────────────────────────────────────────────────────────


def run_out_of_sample_test(
    candles: list[list],
    in_sample_ratio: float = 0.7,
) -> OutOfSampleReport:
    """
    Runs a full out-of-sample test: splits `candles` chronologically,
    selects a strategy using ONLY the in-sample period, then evaluates
    that same strategy (fresh instance, same class/config) on the
    disjoint out-of-sample period it had no influence over.

    Data leakage prevention:
      - The split itself is strictly chronological (see
        split_in_out_of_sample()) — never randomized, so no
        out-of-sample candle can end up earlier than any in-sample one.
      - backtest_all() (strategy selection) is called with ONLY
        split.in_sample — the out-of-sample candles are not created
        until after selection has already happened, so they cannot
        possibly influence which strategy is chosen.
      - The out-of-sample evaluation uses a brand-new strategy instance
        from _build_strategy(), never the mutated instance that ran
        the in-sample backtest — so no signal-dedup state or other
        internal state carries across the two periods.

    Args:
        candles: Full OHLCV history, sorted oldest -> newest.
        in_sample_ratio: Fraction allocated to in-sample selection.
                         See split_in_out_of_sample().

    Returns:
        OutOfSampleReport with both periods' BacktestResult and
        PerformanceReport, plus the split itself.

    Raises:
        ValueError: propagated from split_in_out_of_sample() if the
                    ratio or candle count is invalid.
    """
    split = split_in_out_of_sample(candles, in_sample_ratio=in_sample_ratio)

    logger.info(
        "Out-of-sample split: %d in-sample candles, %d out-of-sample "
        "candles (ratio=%.2f)",
        len(split.in_sample),
        len(split.out_of_sample),
        in_sample_ratio,
    )

    # SELECTION — in-sample only. backtest_all() never sees
    # split.out_of_sample at all in this call.
    in_sample_results = backtest_all(split.in_sample)
    winning_strategy, in_sample_result, in_sample_report = in_sample_results[0]

    logger.info(
        "In-sample winner: %s (sharpe=%.4f, trades=%d)",
        winning_strategy.name,
        in_sample_report.sharpe_ratio,
        in_sample_report.num_trades,
    )

    # EVALUATION — fresh instance of the SAME class/config, run on the
    # disjoint out-of-sample period only.
    oos_strategy = _build_strategy(type(winning_strategy))
    analyser = PerformanceAnalyser()
    oos_backtester = Backtester(
        strategy=oos_strategy,
        initial_capital=settings.initial_capital,
        position_size=settings.position_size,
        slippage_pct=settings.slippage_pct,
        symbol=settings.symbol,
        cost_model=_default_cost_model(),
    )
    out_of_sample_result = oos_backtester.run(split.out_of_sample)
    out_of_sample_report = analyser.analyse(out_of_sample_result)

    logger.info(
        "Out-of-sample result: %s (sharpe=%.4f, trades=%d)",
        oos_strategy.name,
        out_of_sample_report.sharpe_ratio,
        out_of_sample_report.num_trades,
    )

    return OutOfSampleReport(
        strategy_name=winning_strategy.name,
        split=split,
        in_sample_result=in_sample_result,
        in_sample_report=in_sample_report,
        out_of_sample_result=out_of_sample_result,
        out_of_sample_report=out_of_sample_report,
    )


# ──────────────────────────────────────────────────────────────
# WALK-FORWARD TEST
#
# Repeated out-of-sample testing across a rolling sequence of
# train/test windows (see backtester/walk_forward.py for the pure
# window-generation and aggregation logic). Per window, this applies
# the exact same leakage-prevention pattern as
# run_out_of_sample_test() above: backtest_all() (selection) sees only
# that window's train candles, and evaluation uses a fresh
# _build_strategy() instance on that window's disjoint test candles —
# never the mutated selection instance, and never shared across
# windows either.
# ──────────────────────────────────────────────────────────────


def run_walk_forward_test(
    candles: list[list],
    train_window_size: int,
    test_window_size: int,
    step_size: int | None = None,
) -> WalkForwardReport:
    """
    Runs a full walk-forward test: generates a rolling sequence of
    train/test windows, and for each one selects a strategy using ONLY
    that window's train period (via backtest_all(), unchanged) then
    evaluates a fresh instance of the winner (via _build_strategy(),
    unchanged) on that window's disjoint test period. Results are
    aggregated into a WalkForwardReport.

    Data leakage prevention (per window, identical guarantees to
    run_out_of_sample_test(), repeated across every window):
      - generate_walk_forward_windows() produces strictly chronological,
        non-overlapping train/test pairs within each window.
      - backtest_all() (selection) is called with ONLY that window's
        train candles.
      - Test-period evaluation uses a brand-new strategy instance from
        _build_strategy() — never the mutated instance that ran that
        window's own train-period selection, and never shared with any
        other window either (each window gets its own fresh instance).

    Args:
        candles: Full OHLCV history, sorted oldest -> newest.
        train_window_size: Candles per training window.
        test_window_size: Candles per test window.
        step_size: Window advance per step. Defaults to
                   test_window_size. See generate_walk_forward_windows().

    Returns:
        WalkForwardReport with one WalkForwardWindowResult per window
        plus aggregate statistics.

    Raises:
        ValueError: if the parameters are invalid (propagated from
                    generate_walk_forward_windows()), or if there isn't
                    enough data to form even one window.
    """
    windows = generate_walk_forward_windows(
        candles,
        train_window_size=train_window_size,
        test_window_size=test_window_size,
        step_size=step_size,
    )
    if not windows:
        raise ValueError(
            f"Not enough candles ({len(candles)}) to form even one walk-forward "
            f"window (need at least {train_window_size + test_window_size})."
        )

    logger.info(
        "Walk-forward test: %d windows (train=%d, test=%d, step=%d)",
        len(windows),
        train_window_size,
        test_window_size,
        step_size or test_window_size,
    )

    analyser = PerformanceAnalyser()
    window_results: list[WalkForwardWindowResult] = []

    for window in windows:
        # SELECTION — this window's train period only.
        train_results = backtest_all(window.train)
        winning_strategy, train_result, train_report = train_results[0]

        # EVALUATION — fresh instance, this window's test period only.
        test_strategy = _build_strategy(type(winning_strategy))
        test_backtester = Backtester(
            strategy=test_strategy,
            initial_capital=settings.initial_capital,
            position_size=settings.position_size,
            slippage_pct=settings.slippage_pct,
            symbol=settings.symbol,
            cost_model=_default_cost_model(),
        )
        test_result = test_backtester.run(window.test)
        test_report = analyser.analyse(test_result)

        logger.info(
            "  Window %d: train_winner=%s train_sharpe=%.4f -> "
            "test_trades=%d test_pnl=%+.2f test_sharpe=%.4f",
            window.window_index,
            winning_strategy.name,
            train_report.sharpe_ratio,
            test_report.num_trades,
            test_report.total_pnl,
            test_report.sharpe_ratio,
        )

        window_results.append(
            WalkForwardWindowResult(
                window=window,
                strategy_name=winning_strategy.name,
                train_result=train_result,
                train_report=train_report,
                test_result=test_result,
                test_report=test_report,
            )
        )

    return WalkForwardReport(window_results=window_results)


# ──────────────────────────────────────────────────────────────
# PRINT COMPARISON
# ──────────────────────────────────────────────────────────────


def print_comparison(
    results: list[tuple[BaseStrategy, BacktestResult, PerformanceReport]],
) -> None:
    """Prints side-by-side strategy comparison."""
    print("\n" + "=" * 65)
    print("  STRATEGY COMPARISON")
    print("=" * 65)
    print(f"  {'Strategy':<30} {'Trades':>6} {'PnL':>10} {'Sharpe':>8} {'WinRate':>8}")
    print("-" * 65)
    for strategy, result, report in results:
        print(
            f"  {strategy.name:<30} "
            f"{report.num_trades:>6} "
            f"{report.total_pnl:>+10.2f} "
            f"{report.sharpe_ratio:>8.4f} "
            f"{report.win_rate:>7.1%}"
        )
    print("=" * 65)
    print(f"\n  Best strategy : {results[0][0].name}")
    print(f"  Sharpe ratio  : {results[0][2].sharpe_ratio:.4f}")
    print(f"  Total PnL     : {results[0][2].total_pnl:+.2f} USDC\n")


# ──────────────────────────────────────────────────────────────
# PAPER TRADE
# ──────────────────────────────────────────────────────────────


def paper_trade(
    strategy: BaseStrategy,
    ohlcv: OHLCVProvider,
    cost_model: CostModel | None = None,
    kill_path: str | None = None,
) -> None:
    """Paper trades the given strategy using settings from config.

    Fills go through `cost_model` (same shared model as the backtester —
    see backtester.costs): the live tick close stays the reference
    price (paper fill timing is unchanged — no next-open shifting),
    and slippage/fees/funding are applied to it. Defaults to
    CostModel(slippage_pct=settings.slippage_pct), i.e. slippage only.

    Risk state persists through a fail-closed RiskGate (see
    core.risk_state): an engaged kill switch or untrusted state blocks
    new simulated fills. `kill_path` overrides the kill-switch file
    location (tests and operators); defaults to storage/KILL_SWITCH.
    """
    costs = cost_model if cost_model is not None else _default_cost_model()
    logger.info(
        "Starting paper trade: %s for %.1f hours",
        strategy.name,
        settings.run_hours,
    )

    try:
        db = Database()
    except Exception as e:
        logger.error("Cannot open risk database at startup — refusing to trade: %s", e)
        return

    trade_log = TradeLogger(
        log_dir=settings.log_dir,
        symbol=settings.symbol,
        strategy=strategy.name,
        db=db,
    )
    risk = RiskManager(
        account_balance=settings.initial_capital,
        max_position_pct=settings.max_position_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_open_positions=settings.max_open_positions,
    )

    trade_log.log_session_start(
        balance=settings.initial_capital,
        extra={"strategy": strategy.name, "mode": "paper_trade"},
    )

    gate = RiskGate(
        db=db,
        kill_path=kill_path,
        symbol=settings.symbol,
        mode="paper_trade",
        session_id=trade_log.session_id,
    )
    block_reason = gate.startup()
    if block_reason is not None:
        logger.error("Risk gate blocked paper trading at startup: %s", block_reason)
        trade_log.log_session_end(
            balance=settings.initial_capital, total_pnl=0.0, num_trades=0
        )
        return

    in_position = False
    entry_price = 0.0
    entry_fee = 0.0
    entry_time = ""
    entry_ms = 0
    total_pnl = gate.state.total_pnl
    num_trades = gate.state.num_trades
    daily_loss = gate.state.realized_loss
    daily_loss_date = _current_utc_date()
    last_signal = "HOLD"
    kill_logged = False

    if gate.state.open_qty != 0.0:
        # Simulated fills have no external reality: a stale paper intent
        # from a crashed run cannot be resumed against anything real, so
        # it is discarded loudly (counters above are still resumed).
        logger.warning(
            "Discarding stale simulated position intent (qty=%s) — "
            "paper positions cannot survive a restart.",
            gate.state.open_qty,
        )
        gate.state.open_qty = 0.0
        gate.state.entry_price = 0.0
        gate.state.entry_fee = 0.0
        gate.state.basis_known = False
        if not gate.persist():
            logger.error("Cannot persist cleaned risk state — refusing to trade.")
            trade_log.log_session_end(
                balance=settings.initial_capital,
                total_pnl=total_pnl,
                num_trades=num_trades,
            )
            return

    run_seconds = settings.run_hours * 3600
    start = time.time()

    try:
        while time.time() - start < run_seconds:
            if shutdown_requested():
                logger.warning(
                    "Shutdown requested — stopping paper trade without "
                    "starting another iteration."
                )
                break
            try:
                # "Daily" loss must mean per-calendar-day, not
                # cumulative for the life of a long-running process —
                # reset when the UTC date rolls over so a bad day
                # doesn't permanently block trading on every day after.
                new_daily_loss, new_daily_loss_date = _reset_daily_loss_if_new_day(
                    daily_loss, daily_loss_date
                )
                if new_daily_loss_date != daily_loss_date:
                    logger.info(
                        "New UTC day (%s) — resetting daily loss counter "
                        "(was %.2f).",
                        new_daily_loss_date,
                        daily_loss,
                    )
                daily_loss, daily_loss_date = new_daily_loss, new_daily_loss_date

                block_reason = gate.check()
                if block_reason is not None:
                    if not kill_logged:
                        logger.warning(
                            "Risk gate engaged (%s) — skipping trading ticks "
                            "until cleared by the operator.",
                            block_reason,
                        )
                        kill_logged = True
                    time.sleep(settings.sleep_seconds)
                    continue

                candles = ohlcv.fetch(
                    settings.symbol,
                    interval=settings.interval,
                    limit=50,
                )
                if not candles:
                    logger.warning("No candles — skipping tick")
                    time.sleep(settings.sleep_seconds)
                    continue

                candles = clean_data(candles)
                closes = [c[4] for c in candles]
                price = closes[-1]

                # One shared rule (strategies.protocols.dispatch_signal):
                # candle-based strategies (e.g. VWAP) get the full OHLCV
                # window, others get closes — same as
                # Backtester._generate_signals, so backtest and live
                # paper-trading behavior stay consistent.
                signal = dispatch_signal(strategy, candles, closes)

                if signal != "HOLD" or last_signal != "HOLD":
                    trade_log.log_signal(signal, price=price)

                logger.info(
                    "[%s] Price=%.2f Signal=%s InPosition=%s PnL=%+.2f",
                    strategy.name,
                    price,
                    signal,
                    in_position,
                    total_pnl,
                )
                last_signal = signal

                # BUY
                if signal == "BUY" and not in_position:
                    risk_result = risk.check_trade(
                        symbol=settings.symbol,
                        price=price,
                        requested_size=settings.position_size,
                        current_daily_loss=daily_loss,
                        open_positions=1 if in_position else 0,
                    )
                    if risk_result.allowed:
                        gate_reason = gate.check()
                        if gate_reason is not None:
                            logger.warning(
                                "Risk gate engaged at order boundary (%s) — "
                                "BUY skipped.",
                                gate_reason,
                            )
                            trade_log.log_risk_block(
                                reason=f"kill engaged: {gate_reason}",
                                requested_size=settings.position_size,
                            )
                            time.sleep(settings.sleep_seconds)
                            continue
                        entry_price = costs.buy_fill_price(price)
                        entry_fee = costs.fee_for_fill(
                            entry_price, settings.position_size
                        )
                        entry_time = _now_iso()
                        entry_ms = int(time.time() * 1000)
                        in_position = True
                        trade_log.log_order(
                            "BUY", price=price, size=settings.position_size
                        )
                        trade_log.log_fill(
                            "BUY",
                            price=entry_price,
                            size=settings.position_size,
                            fee=entry_fee,
                        )
                        logger.info(
                            "PAPER BUY: %.4f %s @ %.2f",
                            settings.position_size,
                            settings.symbol,
                            entry_price,
                        )
                        if not gate.record(
                            loss_add=0.0,
                            open_qty=settings.position_size,
                            entry_price=entry_price,
                            entry_fee=entry_fee,
                            basis_known=True,
                            total_pnl=total_pnl,
                            num_trades=num_trades,
                        ):
                            logger.error(
                                "Risk-state persist failed after BUY — "
                                "state stale; further trading blocked until "
                                "a persist succeeds."
                            )
                    else:
                        trade_log.log_risk_block(
                            reason=risk_result.reason,
                            requested_size=settings.position_size,
                        )
                        logger.warning("Risk blocked: %s", risk_result.reason)

                # SELL
                elif signal == "SELL" and in_position:
                    gate_reason = gate.check()
                    if gate_reason is not None:
                        logger.error(
                            "Risk gate engaged at order boundary (%s) — SELL "
                            "skipped. Position left open; no fill recorded.",
                            gate_reason,
                        )
                        trade_log.log_error(
                            f"SELL skipped, kill engaged: {gate_reason}",
                            context={"entry_price": entry_price},
                        )
                        time.sleep(settings.sleep_seconds)
                        continue
                    exit_price = costs.sell_fill_price(price)
                    sell_fee = costs.fee_for_fill(exit_price, settings.position_size)
                    funding = costs.funding_for_hold(
                        entry_price * settings.position_size,
                        int(time.time() * 1000) - entry_ms,
                    )
                    fee_paid = entry_fee + sell_fee
                    pnl = (
                        (exit_price - entry_price) * settings.position_size
                        - fee_paid
                        - funding
                    )
                    pnl_pct = (exit_price - entry_price) / entry_price

                    in_position = False
                    total_pnl += pnl
                    num_trades += 1
                    if pnl < 0:
                        daily_loss += abs(pnl)

                    trade_log.log_order(
                        "SELL", price=price, size=settings.position_size
                    )
                    trade_log.log_fill(
                        "SELL",
                        price=exit_price,
                        size=settings.position_size,
                        fee=sell_fee,
                    )
                    trade_log.log_trade_closed(
                        side="LONG",
                        entry_price=entry_price,
                        exit_price=exit_price,
                        size=settings.position_size,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        entry_time=entry_time,
                    )
                    risk.update_balance(settings.initial_capital + total_pnl)
                    if not gate.record(
                        loss_add=abs(pnl) if pnl < 0 else 0.0,
                        open_qty=0.0,
                        entry_price=0.0,
                        entry_fee=0.0,
                        basis_known=False,
                        total_pnl=total_pnl,
                        num_trades=num_trades,
                    ):
                        logger.error(
                            "Risk-state persist failed after SELL — state "
                            "stale; further trading blocked until a persist "
                            "succeeds."
                        )
                    logger.info(
                        "PAPER SELL: %.4f %s @ %.2f | PnL=%+.4f USDC (%+.2f%%)",
                        settings.position_size,
                        settings.symbol,
                        exit_price,
                        pnl,
                        pnl_pct * 100,
                    )

            except NetworkError as e:
                logger.warning("Network error — retrying next tick: %s", e)
                trade_log.log_error(str(e), context={"type": "NetworkError"})
            except APIError as e:
                logger.error("API error: %s", e)
                trade_log.log_error(str(e), context={"type": "APIError"})
            except Exception as e:
                logger.exception("Unexpected error: %s", e)
                trade_log.log_error(str(e), context={"type": "Exception"})

            elapsed = time.time() - start
            remaining = run_seconds - elapsed
            logger.info(
                "Sleeping %ds | Remaining: %.0fm",
                settings.sleep_seconds,
                remaining / 60,
            )
            time.sleep(settings.sleep_seconds)

    except KeyboardInterrupt:
        logger.info("Stopped by user.")

    finally:
        if in_position:
            try:
                candles = ohlcv.fetch(
                    settings.symbol, interval=settings.interval, limit=2
                )
                if candles:
                    final_price = costs.sell_fill_price(candles[-1][4])
                    sell_fee = costs.fee_for_fill(final_price, settings.position_size)
                    funding = costs.funding_for_hold(
                        entry_price * settings.position_size,
                        int(time.time() * 1000) - entry_ms,
                    )
                    fee_paid = entry_fee + sell_fee
                    pnl = (
                        (final_price - entry_price) * settings.position_size
                        - fee_paid
                        - funding
                    )
                    total_pnl += pnl
                    num_trades += 1
                    trade_log.log_trade_closed(
                        "LONG",
                        entry_price,
                        final_price,
                        settings.position_size,
                        pnl,
                        pnl / (entry_price * settings.position_size),
                        entry_time,
                    )
                    logger.info("Force closed at %.2f | PnL=%+.4f", final_price, pnl)
                    # loss_add=0.0 mirrors the existing daily-loss gap:
                    # force-closed PnL bypasses the daily loss counter
                    # (known limitation, unchanged by P5).
                    if not gate.record(
                        loss_add=0.0,
                        open_qty=0.0,
                        entry_price=0.0,
                        entry_fee=0.0,
                        basis_known=False,
                        total_pnl=total_pnl,
                        num_trades=num_trades,
                    ):
                        logger.error("Risk-state persist failed after force-close.")
            except Exception as e:
                logger.error("Failed to force close: %s", e)

        gate.persist()
        trade_log.log_session_end(
            balance=settings.initial_capital + total_pnl,
            total_pnl=total_pnl,
            num_trades=num_trades,
        )
        logger.info(
            "Session ended — trades=%d total_pnl=%+.4f USDC",
            num_trades,
            total_pnl,
        )


# ──────────────────────────────────────────────────────────────
# LIVE TESTNET EXECUTION
#
# NOT paper trading. Every BUY/SELL signal that passes risk checks
# here becomes a REAL order on Hyperliquid TESTNET via TestnetExecutor
# (execution/testnet_executor.py), which independently refuses to
# construct unless IS_MAINNET=False, the client is pointed at the
# testnet endpoint, and ENABLE_TESTNET_LIVE_EXECUTION=true. This
# function never fabricates signals — it only ever acts on whatever
# the strategy actually produces, including HOLD.
# ──────────────────────────────────────────────────────────────


def live_testnet_trade(
    strategy: BaseStrategy,
    ohlcv: OHLCVProvider,
    executor: TestnetExecutor,
    kill_path: str | None = None,
) -> None:
    """
    Runs `strategy` against REAL Hyperliquid TESTNET execution.

    Mirrors paper_trade()'s signal-dispatch loop, but every BUY/SELL
    that clears the risk check is submitted as a real order through
    `executor`, and internal position state is only ever updated after
    a CONFIRMED fill — never assumed from the signal or requested size.

    A fail-closed RiskGate (see core.risk_state) blocks new submissions
    when killed or untrusted, and the exchange position is reconciled
    against persisted intent at startup. `kill_path` overrides the
    kill-switch file location; defaults to storage/KILL_SWITCH.

    Args:
        strategy: Strategy instance to run (its own generate_signal /
                  generate_signal_from_candles decides BUY/SELL/HOLD —
                  never forced).
        ohlcv:    OHLCVProvider for fetching live candles.
        executor: A constructed TestnetExecutor (construction itself
                  proves the safety checks passed).
    """
    logger.warning(
        "Starting REAL TESTNET execution: %s for %.1f hours — this will "
        "place ACTUAL orders on Hyperliquid testnet.",
        strategy.name,
        settings.run_hours,
    )

    try:
        db = Database()
    except Exception as e:
        logger.error("Cannot open risk database at startup — refusing to trade: %s", e)
        return

    trade_log = TradeLogger(
        log_dir=settings.log_dir,
        symbol=settings.symbol,
        strategy=strategy.name,
        db=db,
    )
    risk = RiskManager(
        account_balance=settings.initial_capital,
        max_position_pct=settings.max_position_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_open_positions=settings.max_open_positions,
    )

    trade_log.log_session_start(
        balance=settings.initial_capital,
        extra={"strategy": strategy.name, "mode": "testnet_live_execution"},
    )

    gate = RiskGate(
        db=db,
        kill_path=kill_path,
        symbol=settings.symbol,
        mode="testnet_live_execution",
        session_id=trade_log.session_id,
    )
    block_reason = gate.startup()
    if block_reason is not None:
        logger.error("Risk gate blocked live trading at startup: %s", block_reason)
        trade_log.log_session_end(
            balance=settings.initial_capital, total_pnl=0.0, num_trades=0
        )
        return

    # Position state reflects the REAL account at startup — never
    # assumed to be flat just because this process just started.
    # The exchange quantity is authoritative; persisted intent supplies
    # the cost basis only after reconciliation accepts it.
    exchange_qty = abs(executor.get_position_size())
    in_position = exchange_qty != 0.0
    filled_qty = exchange_qty
    entry_price = 0.0
    entry_fee = 0.0
    reconcile_reason = gate.reconcile_exchange(exchange_qty)
    if reconcile_reason is not None:
        gate.engage(reconcile_reason)
        logger.error(
            "Exchange/persisted position mismatch at startup (%s) — "
            "kill latched. Exchange qty=%s %s, open orders exist=%s. "
            "Resolve manually; no orders will be submitted.",
            reconcile_reason,
            exchange_qty,
            settings.symbol,
            executor.has_open_orders(),
        )
        trade_log.log_session_end(
            balance=settings.initial_capital, total_pnl=0.0, num_trades=0
        )
        return
    if in_position:
        entry_price = gate.state.entry_price
        entry_fee = gate.state.entry_fee
        filled_qty = gate.state.open_qty
    daily_loss = gate.state.realized_loss
    total_pnl = gate.state.total_pnl
    daily_loss_date = _current_utc_date()
    num_trades = gate.state.num_trades
    last_signal = "HOLD"
    kill_logged = False

    run_seconds = settings.run_hours * 3600
    start = time.time()

    try:
        while time.time() - start < run_seconds:
            if shutdown_requested():
                logger.warning(
                    "Shutdown requested — stopping live testnet trade "
                    "without starting another iteration."
                )
                break
            try:
                # "Daily" loss must mean per-calendar-day, not
                # cumulative for the life of a long-running process —
                # reset when the UTC date rolls over so a bad day
                # doesn't permanently block REAL trading on every day
                # after (a much higher-stakes version of the same gap
                # already fixed in paper_trade()).
                new_daily_loss, new_daily_loss_date = _reset_daily_loss_if_new_day(
                    daily_loss, daily_loss_date
                )
                if new_daily_loss_date != daily_loss_date:
                    logger.info(
                        "New UTC day (%s) — resetting daily loss counter "
                        "(was %.2f).",
                        new_daily_loss_date,
                        daily_loss,
                    )
                daily_loss, daily_loss_date = new_daily_loss, new_daily_loss_date

                block_reason = gate.check()
                if block_reason is not None:
                    if not kill_logged:
                        logger.warning(
                            "Risk gate engaged (%s) — skipping trading ticks "
                            "until cleared by the operator.",
                            block_reason,
                        )
                        kill_logged = True
                    time.sleep(settings.sleep_seconds)
                    continue

                candles = ohlcv.fetch(
                    settings.symbol, interval=settings.interval, limit=50
                )
                if not candles:
                    logger.warning("No candles — skipping tick")
                    time.sleep(settings.sleep_seconds)
                    continue

                candles = clean_data(candles)
                closes = [c[4] for c in candles]
                price = closes[-1]

                # Same shared dispatch as paper_trade() (see above).
                signal = dispatch_signal(strategy, candles, closes)

                if signal != "HOLD" or last_signal != "HOLD":
                    trade_log.log_signal(signal, price=price)
                logger.info(
                    "[%s] Price=%.2f Signal=%s InPosition=%s",
                    strategy.name,
                    price,
                    signal,
                    in_position,
                )
                last_signal = signal

                # BUY — never while already long, never a duplicate if
                # an order for this symbol is already open.
                if signal == "BUY" and not in_position:
                    if executor.has_open_orders():
                        logger.warning(
                            "Open order already exists for %s — skipping "
                            "BUY this tick.",
                            settings.symbol,
                        )
                    else:
                        risk_result = risk.check_trade(
                            symbol=settings.symbol,
                            price=price,
                            requested_size=settings.position_size,
                            current_daily_loss=daily_loss,
                            open_positions=1 if in_position else 0,
                        )
                        if risk_result.allowed:
                            gate_reason = gate.check()
                            if gate_reason is not None:
                                logger.warning(
                                    "Risk gate engaged at order boundary (%s) — "
                                    "BUY skipped.",
                                    gate_reason,
                                )
                                trade_log.log_risk_block(
                                    reason=f"kill engaged: {gate_reason}",
                                    requested_size=settings.position_size,
                                )
                                time.sleep(settings.sleep_seconds)
                                continue
                            oid = executor.submit_market_order(
                                is_buy=True, size=risk_result.position_size
                            )
                            trade_log.log_order(
                                "BUY",
                                price=price,
                                size=risk_result.position_size,
                                order_type="MARKET",
                            )
                            fill = executor.wait_for_fill(oid, side="BUY")
                            if fill.status == "filled":
                                # Position state updated ONLY here, on
                                # confirmed fill — and using the actual
                                # filled size, not the requested size.
                                filled_qty = (
                                    fill.filled_size or risk_result.position_size
                                )
                                entry_price = fill.avg_price or price
                                in_position = True
                                entry_fee = 0.0
                                trade_log.log_fill(
                                    "BUY", price=entry_price, size=filled_qty, oid=oid
                                )
                                if not gate.record(
                                    loss_add=0.0,
                                    open_qty=filled_qty,
                                    entry_price=entry_price,
                                    entry_fee=entry_fee,
                                    basis_known=True,
                                    total_pnl=total_pnl,
                                    num_trades=num_trades,
                                ):
                                    logger.error(
                                        "Risk-state persist failed after BUY — "
                                        "state stale; further trading blocked "
                                        "until a persist succeeds."
                                    )
                                logger.info(
                                    "TESTNET BUY FILLED: %.6f %s @ %.2f",
                                    filled_qty,
                                    settings.symbol,
                                    entry_price,
                                )
                            else:
                                trade_log.log_error(
                                    f"BUY not filled: {fill.status}",
                                    context={"oid": oid},
                                )
                                logger.error(
                                    "TESTNET BUY did not fill (status=%s) — "
                                    "position state unchanged.",
                                    fill.status,
                                )
                        else:
                            trade_log.log_risk_block(
                                reason=risk_result.reason,
                                requested_size=settings.position_size,
                            )
                            logger.warning("Risk blocked: %s", risk_result.reason)

                # SELL — never when there is no confirmed position.
                elif signal == "SELL" and in_position:
                    gate_reason = gate.check()
                    if gate_reason is not None:
                        logger.error(
                            "Risk gate engaged at order boundary (%s) — SELL "
                            "skipped. OPEN POSITION left: %s %s — resolve "
                            "manually.",
                            gate_reason,
                            filled_qty,
                            settings.symbol,
                        )
                        trade_log.log_error(
                            f"SELL skipped, kill engaged: {gate_reason}",
                            context={"remaining_position": filled_qty},
                        )
                        time.sleep(settings.sleep_seconds)
                        continue
                    oid = executor.submit_market_order(is_buy=False, size=filled_qty)
                    trade_log.log_order(
                        "SELL", price=price, size=filled_qty, order_type="MARKET"
                    )
                    fill = executor.wait_for_fill(oid, side="SELL")
                    if fill.status == "filled":
                        exit_price = fill.avg_price or price
                        pnl = (exit_price - entry_price) * filled_qty
                        pnl_pct = (
                            (exit_price - entry_price) / entry_price
                            if entry_price
                            else 0.0
                        )
                        total_pnl += pnl
                        num_trades += 1
                        if pnl < 0:
                            daily_loss += abs(pnl)

                        trade_log.log_fill(
                            "SELL", price=exit_price, size=filled_qty, oid=oid
                        )
                        trade_log.log_trade_closed(
                            side="LONG",
                            entry_price=entry_price,
                            exit_price=exit_price,
                            size=filled_qty,
                            pnl=pnl,
                            pnl_pct=pnl_pct,
                        )
                        risk.update_balance(settings.initial_capital + total_pnl)
                        if not gate.record(
                            loss_add=abs(pnl) if pnl < 0 else 0.0,
                            open_qty=0.0,
                            entry_price=0.0,
                            entry_fee=0.0,
                            basis_known=False,
                            total_pnl=total_pnl,
                            num_trades=num_trades,
                        ):
                            logger.error(
                                "Risk-state persist failed after SELL — state "
                                "stale; further trading blocked until a persist "
                                "succeeds."
                            )
                        in_position = False
                        filled_qty = 0.0
                        logger.info(
                            "TESTNET SELL FILLED: @ %.2f | PnL=%+.4f USDC (%+.2f%%)",
                            exit_price,
                            pnl,
                            pnl_pct * 100,
                        )
                    else:
                        remaining = executor.get_position_size()
                        trade_log.log_error(
                            f"SELL not filled: {fill.status}",
                            context={"oid": oid, "remaining_position": remaining},
                        )
                        logger.error(
                            "TESTNET SELL did not fill (status=%s). Remaining "
                            "OPEN TESTNET POSITION: %s %s — resolve manually. "
                            "Position state left unchanged (still in_position).",
                            fill.status,
                            remaining,
                            settings.symbol,
                        )

            except NetworkError as e:
                logger.warning("Network error — retrying next tick: %s", e)
                trade_log.log_error(str(e), context={"type": "NetworkError"})
            except APIError as e:
                logger.error("API error: %s", e)
                trade_log.log_error(str(e), context={"type": "APIError"})
            except TestnetExecutionError as e:
                logger.error("Execution error: %s", e)
                trade_log.log_error(str(e), context={"type": "TestnetExecutionError"})
            except Exception as e:
                # Real money is at stake here — an unexpected error
                # (a RiskManager validation error, a malformed API
                # response that slipped past every other safeguard,
                # anything) must never silently kill the whole live
                # session. Log it, record it, and keep the loop alive
                # for the next tick — mirroring paper_trade()'s
                # existing broad-except behavior, which this function
                # was previously missing despite handling real orders.
                logger.exception("Unexpected error in live testnet tick: %s", e)
                trade_log.log_error(str(e), context={"type": "Exception"})

            time.sleep(settings.sleep_seconds)

    except KeyboardInterrupt:
        logger.info("Stopped by user.")

    finally:
        remaining_position = executor.get_position_size()
        if remaining_position != 0.0:
            logger.warning(
                "Session ending with an OPEN TESTNET POSITION: %s %s — "
                "this is NOT force-closed automatically. Close it manually "
                "or via the smoke test / a manual SELL.",
                remaining_position,
                settings.symbol,
            )
        gate.persist()
        trade_log.log_session_end(
            balance=settings.initial_capital + total_pnl,
            total_pnl=total_pnl,
            num_trades=num_trades,
        )
        logger.info(
            "Session ended — trades=%d total_pnl=%+.4f USDC remaining_position=%s",
            num_trades,
            total_pnl,
            remaining_position,
        )


# ──────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ──────────────────────────────────────────────────────────────


def run_pipeline() -> None:
    """
    Full pipeline: connect → fetch → clean → backtest → compare → trade.

    The final stage is EITHER paper_trade() OR live_testnet_trade() —
    both already-existing, already-tested functions, never a new
    execution path. Which one runs is controlled entirely by the
    existing master safety switch settings.enable_testnet_live_execution
    (ENABLE_TESTNET_LIVE_EXECUTION in .env, defaults False):

      - False (default): paper_trade() — simulated, no real orders.
      - True: live_testnet_trade() — REAL Hyperliquid TESTNET orders,
        via the same TestnetExecutor / run_smoke_cycle-style
        confirmed-fill logic already proven out by
        execution/smoke_test.py. TestnetExecutor's own constructor
        independently re-verifies IS_MAINNET=False and the testnet
        base URL before allowing any order to be placed — this
        function does not duplicate that check, it just surfaces the
        resulting TestnetSafetyError cleanly if it fires.
    """
    print("\n" + "=" * 65)
    print("  HYPER-ENGINE STRATEGY PIPELINE")
    print("=" * 65 + "\n")

    client, symbol_map, ohlcv = connect()
    candles = fetch_data(ohlcv)
    candles = clean_data(candles)

    if len(candles) < 50:
        logger.error("Not enough clean candles (%d). Aborting.", len(candles))
        return

    results = backtest_all(candles)
    print_comparison(results)

    best_strategy, best_result, best_report = results[0]

    if best_report.sharpe_ratio < settings.min_sharpe_to_trade:
        print(
            f"  No trade — best Sharpe {best_report.sharpe_ratio:.4f} "
            f"< minimum {settings.min_sharpe_to_trade}"
        )
        logger.warning(
            "Best Sharpe %.4f below minimum %.1f — no trading.",
            best_report.sharpe_ratio,
            settings.min_sharpe_to_trade,
        )
        return

    if best_result.num_trades == 0:
        print("  No trade — strategy had zero trades on backtest data.")
        logger.warning("Best strategy had zero trades.")
        return

    # IMPORTANT: never hand the backtested strategy instance to
    # paper_trade() / live_testnet_trade(). It ran through the full
    # backtest candle history, so its internal signal-dedup state
    # (_last_signal / _last_crossover) reflects wherever the backtest
    # happened to end — not a fresh "no signal yet" state. Both
    # trading functions start in_position at False, so reusing that
    # mutated instance can suppress the first live signal or fire a
    # SELL with no open position to close. Build a brand-new instance
    # of the same class with the same configuration instead, so the
    # backtester and the live/paper trader never share mutable
    # strategy state.
    fresh_strategy = _build_strategy(type(best_strategy))

    print(f"  Selected : {fresh_strategy.name}")

    if settings.enable_testnet_live_execution:
        print(
            f"  ENABLE_TESTNET_LIVE_EXECUTION=true — starting REAL testnet "
            f"execution ({settings.run_hours}h)...\n"
        )
        try:
            executor = TestnetExecutor(
                client=client, symbol_map=symbol_map, symbol=settings.symbol
            )
        except TestnetSafetyError as e:
            print(f"  ABORTED — testnet execution safety check failed: {e}")
            logger.error("Refusing live testnet execution: %s", e)
            return
        live_testnet_trade(fresh_strategy, ohlcv, executor)
    else:
        print(f"  Starting {settings.run_hours}h paper trade...\n")
        paper_trade(fresh_strategy, ohlcv)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _current_utc_date():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date()


def _reset_daily_loss_if_new_day(
    daily_loss: float,
    daily_loss_date,
    current_date=None,
) -> tuple[float, object]:
    """
    Returns (daily_loss, daily_loss_date), resetting daily_loss to 0.0
    if current_date has moved past daily_loss_date.

    "Daily" loss must mean per-calendar-day, not cumulative for the
    life of a long-running process — without this, a session spanning
    more than one UTC day would permanently block trading on every day
    after the first day the loss cap was hit, since daily_loss was
    previously only ever incremented, never reset.

    Pure function (current_date is injectable for testing) so this
    logic is independently testable without needing to mock the full
    paper_trade()/live_testnet_trade() loop.
    """
    if current_date is None:
        current_date = _current_utc_date()
    if current_date != daily_loss_date:
        return 0.0, current_date
    return daily_loss, daily_loss_date


if __name__ == "__main__":
    run_pipeline()
