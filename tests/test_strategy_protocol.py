"""shared strategy interface protocol + dispatch helper.

Pins that backtester and runner use one identical Protocol object and
one dispatch rule, and that candle/close routing is preserved exactly.
Characterizes (not endorses) the existing full-history vs 50-candle
VWAP window difference. No strategy math changes; no network.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import backtester.backtester as backtester_module
import strategy_runner as runner_module
from backtester.backtester import Backtester
from strategies.base_strategy import BaseStrategy
from strategies.bb_strategy import BollingerStrategy
from strategies.ema_strategy import EMAStrategy
from strategies.protocols import (
    _CandleBasedStrategy,
    dispatch_signal,
)
from strategies.rsi_strategy import RSIStrategy
from strategies.vwap_strategy import VWAPStrategy


def make_candle(ts, open_, high, low, close, volume):
    return [ts, open_, high, low, close, volume]


def closes_only_strategy():
    s = MagicMock(spec=BaseStrategy)
    s.name = "ClosesMock"
    s.generate_signal.side_effect = lambda closes: "HOLD"
    return s


class CandleOnlyStrategy:
    """Candle-only shape with NO generate_signal — the FakeCandleStrategy
    pattern from test_backtester, proving dispatch does not require the
    closes method to exist."""

    def __init__(self):
        self.name = "CandleOnly"
        self.calls: list = []

    def generate_signal_from_candles(self, candles):
        self.calls.append(list(candles))
        return "HOLD"


# ── One shared Protocol object ──


def test_protocol_is_single_shared_object() -> None:
    import strategies.protocols as protocols_module

    assert protocols_module._CandleBasedStrategy is _CandleBasedStrategy
    assert backtester_module.dispatch_signal is dispatch_signal
    assert runner_module.dispatch_signal is dispatch_signal
    # No stale private duplicates remain in either consumer.
    assert not hasattr(backtester_module, "_CandleBasedStrategy")
    assert not hasattr(runner_module, "_CandleBasedStrategy")


# ── Dispatch paths ──


def test_dispatch_routes_close_strategies_to_closes() -> None:
    for make in (
        lambda: EMAStrategy(fast_period=9, slow_period=21),
        lambda: RSIStrategy(),
        lambda: BollingerStrategy(),
        closes_only_strategy,
    ):
        s = make()
        assert not isinstance(s, _CandleBasedStrategy)
        assert (
            dispatch_signal(
                s,
                [make_candle(1000, 10, 11, 9, 10, 100)],
                [10.0],
            )
            == "HOLD"
        )


def test_dispatch_routes_vwap_to_candles() -> None:
    s = VWAPStrategy(mode="crossover")
    assert isinstance(s, _CandleBasedStrategy)
    candles = [
        make_candle(1000, 100.0, 101.0, 99.0, 95.0, 1000.0),
        make_candle(2000, 100.0, 200.0, 100.0, 150.0, 1.0),
    ]
    assert dispatch_signal(s, candles, [c[4] for c in candles]) == "BUY"


def test_dispatch_supports_candle_only_strategy_without_closes_method() -> None:
    s = CandleOnlyStrategy()
    assert isinstance(s, _CandleBasedStrategy)
    candles = [make_candle(1000, 10, 11, 9, 10, 100)]
    assert dispatch_signal(s, candles, [10.0]) == "HOLD"
    assert s.calls == [candles]  # full list forwarded untouched


def test_dispatch_forwards_inputs_untouched() -> None:
    seen: dict = {}
    s = MagicMock(spec=BaseStrategy)
    s.name = "ForwardMock"

    def record(closes):
        seen["closes"] = closes
        return "HOLD"

    s.generate_signal.side_effect = record
    candles = [make_candle(1000, 10, 11, 9, 10, 100)]
    closes = [10.0]
    assert dispatch_signal(s, candles, closes) == "HOLD"
    assert seen["closes"] is closes  # same object, not a copy


# ── Backtester integration: identical signal sequences ──


def test_backtester_prefix_dispatch_matches_direct_calls() -> None:
    """VWAP over growing prefixes via Backtester._generate_signals must
    equal calling the strategy directly on the same prefixes."""
    from fixtures.golden_candles import g2_candles

    candles = g2_candles(n=20)
    b = Backtester(strategy=VWAPStrategy(mode="crossover", num_std=2.0))
    df = b._build_dataframe([list(c) for c in candles])
    df = b._generate_signals(df)
    direct = VWAPStrategy(mode="crossover", num_std=2.0)
    expected = [
        direct.generate_signal_from_candles([list(c) for c in candles[: i + 1]])
        for i in range(len(candles))
    ]
    assert list(df["signal"]) == expected


# ── Window characterization: full-history vs 50-candle VWAP ──


def test_vwap_window_difference_characterized() -> None:
    """Documents existing behavior: VWAP anchors at the START of the
    window it is given, so the same market bar gets different VWAP
    values under a full-history backtest window vs the runner's
    50-candle live window. This test pins that the difference EXISTS
    (it is the current, deliberate window-passing behavior) — not
    that it is desirable."""
    from fixtures.golden_candles import g2_candles

    from indicators.vwap import calculate_vwap

    full = g2_candles(n=120)
    window_50 = full[-50:]
    full_vwap = calculate_vwap([list(c) for c in full], 2.0)[-1].vwap
    window_vwap = calculate_vwap([list(c) for c in window_50], 2.0)[-1].vwap
    # Same last bar, different anchors → different values. If this ever
    # becomes equal, windowing changed and the goldens must be re-examined.
    assert full_vwap != window_vwap
    # Both are sane positive prices on this synthetic series.
    assert full_vwap > 0 and window_vwap > 0
