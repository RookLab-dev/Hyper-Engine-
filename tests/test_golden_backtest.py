"""golden baseline + execution-convention regression tests.

Docs-only-adjacent: these tests pin deterministic backtester behavior.
They change no trading logic. Fixtures live in
tests/fixtures/golden_candles.py (synthetic data only — no network,
no randomness, no wall-clock).

G1 (micro case): expected entry/exit/PnL are calculated BY HAND below
from the backtester's documented convention (signal[i] executes at
open[i+1]; buys fill above by slippage, sells below). If these fail,
the implementation disagrees with its own documented convention — do
NOT "fix" the numbers to match; investigate.

G2 (engine baseline): pins the current deterministic output of the
four real strategies on 120 synthetic candles as a regression
tripwire. G2 values characterize present behavior (they are not a
correctness proof). Per the Phase 2 plan, any intentional change to
G2 goldens requires an explanation, a before/after comparison, and
explicit review — never a silent update.
"""

from __future__ import annotations

from typing import Callable, TypedDict

from unittest.mock import MagicMock

import pytest

from backtester.backtester import Backtester
from fixtures.golden_candles import (
    G1_CANDLES,
    G1_PARAMS,
    G1_SIGNALS,
    g2_candles,
)
from strategies.base_strategy import BaseStrategy
from strategies.bb_strategy import BollingerStrategy
from strategies.ema_strategy import EMAStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.vwap_strategy import VWAPStrategy


class BacktesterKwargs(TypedDict):
    initial_capital: float
    position_size: float
    slippage_pct: float
    symbol: str


def scripted_strategy(signals: list[str]) -> MagicMock:
    """Signal-per-candle stub (same shape as test_backtester.mock_strategy)."""
    strategy = MagicMock(spec=BaseStrategy)
    strategy.name = "GoldenScriptedStrategy"
    strategy.min_periods = 1
    signal_iter = iter(signals)
    strategy.generate_signal.side_effect = lambda closes: next(signal_iter, "HOLD")
    return strategy


# ──────────────────────────────────────────────────────────────
# G1 — hand-calculated micro case
#
# Candles (ts, open, ...): row0 open=100, row1 open=150, row2 open=200,
# row3 open=250, row4 open=300. Signals: BUY@row0, SELL@row2.
# Convention: exec_signal = signal shifted by one row, fill at that
# row's OPEN. So BUY fills at open[1]=150.0, SELL at open[3]=250.0.
# Params: size=2.0, slippage=0.01.
#   entry = 150.0 * 1.01 = 151.5        (buy fills ABOVE reference)
#   exit  = 250.0 * 0.99 = 247.5        (sell fills BELOW reference)
#   pnl   = (247.5 - 151.5) * 2.0 = 192.0
#   pnl%  = 96.0 / 151.5 = 64/101 ≈ 0.63366337
# ──────────────────────────────────────────────────────────────


def test_g1_single_trade_exact_accounting() -> None:
    b = Backtester(
        strategy=scripted_strategy(list(G1_SIGNALS)),
        initial_capital=G1_PARAMS["initial_capital"],
        position_size=G1_PARAMS["position_size"],
        slippage_pct=G1_PARAMS["slippage_pct"],
        symbol=G1_PARAMS["symbol"],
    )
    result = b.run([list(c) for c in G1_CANDLES])

    assert result.num_trades == 1
    assert result.candles_tested == 5
    trade = result.trades[0]
    # Fill indices: BUY at row 1, SELL at row 3 (next-open after signal).
    assert trade.entry_time == 2000
    assert trade.exit_time == 4000
    # Fill prices with slippage applied on the correct side.
    assert trade.entry_price == 150.0 * 1.01
    assert trade.exit_price == 250.0 * 0.99
    assert trade.entry_price == 151.5
    assert trade.exit_price == 247.5
    assert trade.entry_price > 150.0
    assert trade.exit_price < 250.0
    assert trade.size == 2.0
    assert trade.pnl == 192.0
    assert trade.pnl_pct == pytest.approx(64 / 101, rel=1e-12)
    assert result.total_pnl == 192.0
    assert result.win_rate == 1.0
    assert result.equity_curve == [10_000.0, 10_192.0]


def test_g1_zero_slippage_baseline() -> None:
    """Slip=0 locks the cost-free baseline P3's cost model will extend."""
    b = Backtester(
        strategy=scripted_strategy(list(G1_SIGNALS)),
        initial_capital=10_000.0,
        position_size=2.0,
        slippage_pct=0.0,
        symbol="BTC",
    )
    result = b.run([list(c) for c in G1_CANDLES])

    assert result.num_trades == 1
    trade = result.trades[0]
    assert trade.entry_price == 150.0
    assert trade.exit_price == 250.0
    assert trade.pnl == 200.0
    assert result.equity_curve == [10_000.0, 10_200.0]


def test_g1_is_deterministic() -> None:
    """Same inputs twice → identical trades (no hidden state/randomness)."""
    kwargs: BacktesterKwargs = {
        "initial_capital": G1_PARAMS["initial_capital"],
        "position_size": G1_PARAMS["position_size"],
        "slippage_pct": G1_PARAMS["slippage_pct"],
        "symbol": G1_PARAMS["symbol"],
    }
    r1 = Backtester(strategy=scripted_strategy(list(G1_SIGNALS)), **kwargs).run(
        [list(c) for c in G1_CANDLES]
    )
    r2 = Backtester(strategy=scripted_strategy(list(G1_SIGNALS)), **kwargs).run(
        [list(c) for c in G1_CANDLES]
    )
    assert r1.total_pnl == r2.total_pnl
    assert r1.num_trades == r2.num_trades
    assert r1.equity_curve == r2.equity_curve
    assert [(t.entry_price, t.exit_price) for t in r1.trades] == [
        (t.entry_price, t.exit_price) for t in r2.trades
    ]


# ──────────────────────────────────────────────────────────────
# G2 — engine regression baseline (characterization, not proof)
# Fixed: 120 synthetic candles, fresh real-strategy instances,
# initial_capital=10000.0, position_size=0.01, slippage=0.001.
# Values below were pinned from a deterministic characterization run
# (double-run equality verified in-test on every run) and sanity
# checked (final equity == initial + total PnL, equity length ==
# trades + 1, all-winning runs have zero drawdown).
# ──────────────────────────────────────────────────────────────

G2_KWARGS: BacktesterKwargs = {
    "initial_capital": 10_000.0,
    "position_size": 0.01,
    "slippage_pct": 0.001,
    "symbol": "BTC",
}


def _g2_makers() -> dict[str, Callable[[], BaseStrategy]]:
    return {
        "EMA": lambda: EMAStrategy(fast_period=9, slow_period=21),
        "RSI": lambda: RSIStrategy(
            period=14, oversold_threshold=30.0, overbought_threshold=70.0
        ),
        "BB": lambda: BollingerStrategy(period=20, num_std=2.0),
        "VWAP": lambda: VWAPStrategy(mode="crossover", num_std=2.0),
    }


def test_g2_deterministic_across_strategies() -> None:
    candles = g2_candles()
    assert len(candles) == 120
    for name, make in _g2_makers().items():
        r1 = Backtester(strategy=make(), **G2_KWARGS).run([list(c) for c in candles])
        r2 = Backtester(strategy=make(), **G2_KWARGS).run([list(c) for c in candles])
        assert (name, r1.num_trades, r1.total_pnl) == (
            name,
            r2.num_trades,
            r2.total_pnl,
        )
        assert r1.equity_curve == r2.equity_curve
        # Structural invariants holding for every strategy output.
        assert r1.candles_tested == 120
        assert len(r1.equity_curve) == r1.num_trades + 1
        assert r1.equity_curve[-1] == pytest.approx(10_000.0 + r1.total_pnl, rel=1e-9)
        assert 0.0 <= r1.win_rate <= 1.0
        assert r1.max_drawdown >= 0.0


def test_g2_candles_are_valid_ohlc() -> None:
    """Every generated candle must envelope its full open..close range.

    Regression test for the generator bug where high/low were banded
    around the close only: on a sharp drop high could print below the
    open, and on a sharp jump low could print above the open.
    """
    for n in (1, 2, 5, 120):
        candles = g2_candles(n=n)
        assert len(candles) == n
        for ts, open_, high, low, close, volume in candles:
            assert low <= min(open_, close) <= max(open_, close) <= high
            assert volume > 0


def test_g2_ema_golden() -> None:
    result = Backtester(
        strategy=EMAStrategy(fast_period=9, slow_period=21), **G2_KWARGS
    ).run(g2_candles())
    assert result.num_trades == 3
    assert result.total_pnl == pytest.approx(0.4371902, rel=1e-9)
    assert result.equity_curve[-1] == pytest.approx(10_000.4371902, rel=1e-9)
    assert result.win_rate == 1.0
    assert result.max_drawdown == 0.0
    t0 = result.trades[0]
    assert t0.entry_price == pytest.approx(111.33122, rel=1e-9)
    assert t0.exit_price == pytest.approx(122.98689, rel=1e-9)
    assert (t0.entry_time, t0.exit_time) == (1000126000000, 1000226800000)


def test_g2_rsi_golden_no_trades() -> None:
    result = Backtester(
        strategy=RSIStrategy(
            period=14, oversold_threshold=30.0, overbought_threshold=70.0
        ),
        **G2_KWARGS,
    ).run(g2_candles())
    # Smooth synthetic series never leaves the 30/70 band: no signals.
    assert result.num_trades == 0
    assert result.total_pnl == 0.0
    assert result.equity_curve == [10_000.0]
    assert result.max_drawdown == 0.0


def test_g2_bollinger_golden_no_trades() -> None:
    result = Backtester(
        strategy=BollingerStrategy(period=20, num_std=2.0), **G2_KWARGS
    ).run(g2_candles())
    # Smooth synthetic series stays inside the bands: no signals.
    assert result.num_trades == 0
    assert result.total_pnl == 0.0
    assert result.equity_curve == [10_000.0]
    assert result.max_drawdown == 0.0


def test_g2_vwap_golden() -> None:
    result = Backtester(
        strategy=VWAPStrategy(mode="crossover", num_std=2.0), **G2_KWARGS
    ).run(g2_candles())
    assert result.num_trades == 2
    assert result.total_pnl == pytest.approx(0.6615016, rel=1e-9)
    assert result.equity_curve[-1] == pytest.approx(10_000.6615016, rel=1e-9)
    assert result.win_rate == 1.0
    assert result.max_drawdown == 0.0
    t0 = result.trades[0]
    assert t0.entry_price == pytest.approx(102.26216, rel=1e-9)
    assert t0.exit_price == pytest.approx(110.29959, rel=1e-9)
    assert (t0.entry_time, t0.exit_time) == (1000007200000, 1000068400000)
