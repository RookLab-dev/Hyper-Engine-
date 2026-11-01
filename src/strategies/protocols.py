"""Shared strategy structural protocols.

Single home for the candle-based strategy Protocol previously
duplicated (privately, near-verbatim) in backtester.backtester and
strategy_runner. The Protocol itself is unchanged — same method,
same signature, same runtime presence-check semantics.

dispatch_signal() centralizes the one dispatch rule every call site
already used: isinstance(strategy, _CandleBasedStrategy) selects the
full-OHLCV path (generate_signal_from_candles), anything else uses
the closes-only path (generate_signal). Callers keep building the
input lists exactly as before (backtester: DataFrame column order +
prefix slices; runner: fetched window) — the helper only chooses
which method to call, never how the inputs are constructed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class _CandleBasedStrategy(Protocol):
    """
    Structural type for strategies that need full OHLCV data instead of
    just closes (e.g. VWAPStrategy.generate_signal_from_candles) —
    used only so mypy can narrow a strategy's type at dispatch sites.

    @runtime_checkable makes isinstance(x, _CandleBasedStrategy) check
    for the presence of a callable generate_signal_from_candles
    attribute, i.e. the exact same test hasattr(x,
    "generate_signal_from_candles") performed before — this is a
    typing-only construct, not a behavior change.
    """

    def generate_signal_from_candles(self, candles: list[list[float]]) -> str: ...


def is_candle_based(strategy: object) -> bool:
    """True when the strategy consumes full OHLCV candles.

    Single home for the isinstance rule every dispatch site uses, so
    callers can gate candle-list construction without importing the
    Protocol itself.
    """
    return isinstance(strategy, _CandleBasedStrategy)


def dispatch_signal(
    strategy: object,
    candles: list[list],
    closes: list[float],
) -> str:
    """Calls the strategy's signal method for one tick/bar.

    Candle-based strategies (isinstance of _CandleBasedStrategy)
    receive the full candle list via generate_signal_from_candles;
    all others receive the closes list via generate_signal. Inputs
    are forwarded untouched — the caller owns their construction.
    """
    if isinstance(strategy, _CandleBasedStrategy):
        return strategy.generate_signal_from_candles(candles)
    closes_fn = getattr(strategy, "generate_signal", None)
    if not callable(closes_fn):
        raise TypeError(
            "Strategy has neither generate_signal_from_candles nor "
            f"generate_signal: {strategy!r}"
        )
    return closes_fn(closes)
