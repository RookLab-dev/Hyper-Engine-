"""Deterministic golden fixtures for the Phase 0 regression baseline.

No trading logic, no network, no randomness, no
wall-clock — every candle list here is a literal or a pure function of
its inputs, so any test using these fixtures is deterministic by
construction.

- G1: 5 hand-written candles with distinct open prices, paired with a
  scripted ["BUY", "HOLD", "SELL", ...] signal sequence. The expected
  entry/exit/PnL are calculated by hand in tests/test_golden_backtest.py
  against the backtester's documented signal-shift + next-open fill
  convention — never fitted to whatever the code happens to output.
- G2: 120 synthetic hourly candles from a closed-form formula (sine +
  trend, no random component) used to pin the current deterministic
  behavior of the four real strategies as a regression tripwire.
"""

from __future__ import annotations
from typing import TypedDict

import math

BASE_TS = 1_000_000_000_000
HOUR_MS = 3_600_000


class G1Params(TypedDict):
    initial_capital: float
    position_size: float
    slippage_pct: float
    symbol: str


G1_PARAMS: G1Params = {
    "initial_capital": 10_000.0,
    "position_size": 2.0,
    "slippage_pct": 0.01,
    "symbol": "BTC",
}

# G1 candles: [timestamp, open, high, low, close, volume].
# Opens are deliberately distinct (100/150/200/250/300) so a fill at the
# wrong index or a close-to-close fill cannot accidentally match.
# With scripted signals ["BUY", "HOLD", "SELL", "HOLD", "HOLD"]:
#   signal[0]=BUY -> exec at row 1 -> BUY fills at open[1] = 150.0
#   signal[2]=SELL -> exec at row 3 -> SELL fills at open[3] = 250.0
G1_CANDLES: list[list] = [
    [1000, 100.0, 101.0, 99.0, 100.5, 100.0],
    [2000, 150.0, 152.0, 148.0, 151.0, 100.0],
    [3000, 200.0, 202.0, 198.0, 201.0, 100.0],
    [4000, 250.0, 252.0, 248.0, 251.0, 100.0],
    [5000, 300.0, 302.0, 298.0, 301.0, 100.0],
]

G1_SIGNALS = ["BUY", "HOLD", "SELL", "HOLD", "HOLD"]

G2_N = 120


def g2_closes(n: int = G2_N) -> list[float]:
    """Closed-form deterministic close series: trend + sine, no randomness."""
    return [round(100.0 + 0.5 * i + 10.0 * math.sin(i / 6.0), 2) for i in range(n)]


def g2_candles(
    n: int = G2_N,
    start_ts: int = BASE_TS,
    interval_ms: int = HOUR_MS,
) -> list[list]:
    """Hourly OHLCV candles built from g2_closes(). Open = previous close."""
    closes = g2_closes(n)
    candles = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i > 0 else close
        # Envelope the full open..close range: banding around the close
        # alone can leave high below the open on a sharp drop (or low
        # above the open on a sharp jump), producing an invalid candle.
        high = round(max(open_, close) * 1.01, 2)
        low = round(min(open_, close) * 0.99, 2)
        candles.append([start_ts + i * interval_ms, open_, high, low, close, 100.0])
    return candles
