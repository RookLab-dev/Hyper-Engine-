"""fill-timing lock-in for the backtester.

Timing invariant (see Backtester.run: signal column is shifted by one
row and fills price from the next row's OPEN):
  S(i)   = strategy signal computed from candles[:i+1] only (no future data)
  F(i+1) = execution of S(i) at open[i+1] (never the signal candle's prices)
SELL is symmetric. The sole documented exception is the final-candle
force-close: with no next candle in existence, an open position exits
at close[-1] (cost-adjusted) stamped with ts[-1].

Paper trading is intentionally NOT next-open (it fills at the live tick
close) and is covered by tests/test_costs.py, not here. Testnet fills
are exchange truth. These tests pin the backtest convention only.

Any test failing here after an engine change means lookahead or a
same-candle fill was introduced (or the force-close exception moved) —
stop and investigate; do not adjust these expectations to fit.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from typing import TypedDict

from backtester.backtester import Backtester
from fixtures.golden_candles import g2_candles
from strategies.base_strategy import BaseStrategy
from strategies.ema_strategy import EMAStrategy
from strategies.vwap_strategy import VWAPStrategy


class BacktesterKwargs(TypedDict):
    initial_capital: float
    position_size: float
    slippage_pct: float
    symbol: str


def scripted(signals: list[str]) -> MagicMock:
    strategy = MagicMock(spec=BaseStrategy)
    strategy.name = "TimingScriptedStrategy"
    strategy.min_periods = 1
    signal_iter = iter(signals)
    strategy.generate_signal.side_effect = lambda closes: next(signal_iter, "HOLD")
    return strategy


def timing_candles() -> list[list]:
    """Distinct opens/closes per row so a wrong-row fill cannot match."""
    return [
        [1000, 100.0, 101.0, 99.0, 105.0, 10.0],
        [2000, 150.0, 152.0, 148.0, 155.0, 10.0],
        [3000, 200.0, 202.0, 198.0, 205.0, 10.0],
        [4000, 250.0, 252.0, 248.0, 255.0, 10.0],
    ]


def test_buy_executes_at_next_open_not_signal_candle() -> None:
    b = Backtester(
        strategy=scripted(["BUY", "HOLD", "HOLD", "HOLD"]),
        slippage_pct=0.0,
        position_size=1.0,
    )
    result = b.run([list(c) for c in timing_candles()])
    assert result.num_trades == 1
    trade = result.trades[0]
    # BUY signaled at row 0 (ts 1000) must fill at row 1's OPEN…
    assert trade.entry_time == 2000
    assert trade.entry_price == 150.0
    # …never at any price from the signal candle itself.
    assert trade.entry_price != 100.0
    assert trade.entry_price != 105.0


def test_sell_executes_at_next_open_not_signal_candle() -> None:
    b = Backtester(
        strategy=scripted(["BUY", "SELL", "HOLD", "HOLD"]),
        slippage_pct=0.0,
        position_size=1.0,
    )
    result = b.run([list(c) for c in timing_candles()])
    assert result.num_trades == 1
    trade = result.trades[0]
    # SELL signaled at row 1 (ts 2000) must fill at row 2's OPEN…
    assert trade.exit_time == 3000
    assert trade.exit_price == 200.0
    # …never at the signal candle's close, and strictly after entry.
    assert trade.exit_price != 155.0
    assert trade.exit_time > trade.entry_time


def test_force_close_uses_last_close_not_open() -> None:
    """Documented exception: no next candle exists, so an open position
    exits at the final candle's CLOSE (cost-adjusted here by slippage)."""
    b = Backtester(
        strategy=scripted(["BUY", "HOLD", "HOLD", "HOLD"]),
        slippage_pct=0.01,
        position_size=1.0,
    )
    result = b.run([list(c) for c in timing_candles()])
    assert result.num_trades == 1
    trade = result.trades[0]
    assert trade.entry_time == 2000
    assert trade.exit_time == 4000
    assert trade.exit_price == 255.0 * 0.99  # last CLOSE, slip-adjusted
    assert trade.exit_price != 250.0  # not the last OPEN


def _mutated_last_close(candles: list[list], factor: float) -> list[list]:
    """Returns a copy with only the final row's close moved (kept valid)."""
    out = [list(c) for c in candles]
    ts, open_, _high, _low, close, volume = out[-1]
    new_close = round(close * factor, 2)
    out[-1] = [
        ts,
        open_,
        round(max(open_, new_close) * 1.01, 2),
        round(min(open_, new_close) * 0.99, 2),
        new_close,
        volume,
    ]
    return out


def test_future_mutation_leaves_existing_fills_unchanged() -> None:
    """Lookahead tripwire, close-only and candle-based strategies: moving
    the FINAL candle's close must not alter any already-determined fill,
    because signals only ever see candles[:i+1].

    Boundary: a force-closed final trade legitimately exits at the last
    candle's close (the documented exception), so only its exit leg may
    follow the mutation — its entry and every earlier trade must not.
    """
    for make in (
        lambda: EMAStrategy(fast_period=9, slow_period=21),
        lambda: VWAPStrategy(mode="crossover", num_std=2.0),
    ):
        base_candles = g2_candles()
        last_ts = base_candles[-1][0]
        kwargs: BacktesterKwargs = {
            "initial_capital": 10_000.0,
            "position_size": 0.01,
            "slippage_pct": 0.001,
            "symbol": "BTC",
        }
        before = Backtester(strategy=make(), **kwargs).run(
            [list(c) for c in base_candles]
        )
        after = Backtester(strategy=make(), **kwargs).run(
            _mutated_last_close(base_candles, 1.5)
        )
        assert before.num_trades == after.num_trades
        for b, a in zip(before.trades[:-1], after.trades[:-1]):
            assert (b.entry_price, b.exit_price, b.entry_time, b.exit_time) == (
                a.entry_price,
                a.exit_price,
                a.entry_time,
                a.exit_time,
            )
        last_before, last_after = before.trades[-1], after.trades[-1]
        assert (last_before.entry_price, last_before.entry_time) == (
            last_after.entry_price,
            last_after.entry_time,
        )
        if (last_before.exit_price, last_before.exit_time) != (
            last_after.exit_price,
            last_after.exit_time,
        ):
            # The only legitimate cause: both exits are the documented
            # final-candle force-close, which follows the last close.
            assert last_before.exit_time == last_ts
            assert last_after.exit_time == last_ts
