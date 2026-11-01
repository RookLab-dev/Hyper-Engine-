"""Unified execution-cost model (Phase 2, P3).

One cost model shared by backtesting and paper trading so both paths
apply identical cost semantics to their own fills. Testnet execution
(execution/testnet_executor.py) never uses this module: real exchange
fills and fees are ground truth and must never be restated by a model.

Units: prices in quote-per-base (e.g. USDC per BTC), sizes in base
units (e.g. BTC), rates as fractions (0.001 = 0.1%), hold durations in
integer milliseconds, notionals/fees/funding in quote currency (USDC).

Accounting convention (fees and funding kept distinct):
    gross_pnl = (sell_fill - buy_fill) * size          # fills include slippage
    net_pnl   = gross_pnl - fee_paid - funding_paid
i.e. net_pnl + fee_paid + funding_paid == gross_pnl_from_actual_fills.
Slippage is embedded in the fills themselves, not stored separately:
versus a frictionless reference PnL,
    slippage_cost = frictionless_pnl - gross_pnl
where frictionless_pnl = (sell_ref - buy_ref) * size. See
tests/test_costs.py for both identities asserted on hand-computed legs.

Funding is an opt-in linear approximation
(notional * rate * hold_hours), disabled by default (rate 0.0). The
rate is a non-negative cost magnitude applied to LONG positions only
(the engine is long-only); negative (rebate) funding is not modeled.
This does not reproduce any exchange's actual funding schedule —
pass an explicit rate only when that approximation is appropriate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _require_rate(value: object, name: str) -> float:
    """Validates a cost rate: real number, finite, 0 <= r < 1."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    if not 0.0 <= result < 1.0:
        raise ValueError(f"{name} must satisfy 0 <= r < 1, got {result}.")
    return result


def _require_price(value: object, name: str) -> float:
    """Validates a reference/fill price: real number, finite, > 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    if result <= 0.0:
        raise ValueError(f"{name} must be > 0, got {result}.")
    return result


def _require_size(value: object, name: str) -> float:
    """Validates an order size: real number, finite, > 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}.")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    if result <= 0.0:
        raise ValueError(f"{name} must be > 0, got {result}.")
    return result


@dataclass(frozen=True)
class CostModel:
    """Explicit execution-cost assumptions shared across engine paths.

    All rates default to cost-free. A default-constructed
    CostModel(slippage_pct=s) reproduces the historical backtester fill
    math exactly (buy at ref*(1+s), sell at ref*(1-s), no fees/funding),
    which is what keeps the existing golden tests green unmodified.
    """

    slippage_pct: float = 0.0
    fee_pct: float = 0.0
    funding_rate_per_hour: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "slippage_pct", _require_rate(self.slippage_pct, "slippage_pct")
        )
        object.__setattr__(self, "fee_pct", _require_rate(self.fee_pct, "fee_pct"))
        object.__setattr__(
            self,
            "funding_rate_per_hour",
            _require_rate(self.funding_rate_per_hour, "funding_rate_per_hour"),
        )

    def buy_fill_price(self, ref_price: float) -> float:
        """Buy fill from a reference price: fills above (ref * (1 + slip))."""
        ref = _require_price(ref_price, "ref_price")
        return ref * (1.0 + self.slippage_pct)

    def sell_fill_price(self, ref_price: float) -> float:
        """Sell fill from a reference price: fills below (ref * (1 - slip))."""
        ref = _require_price(ref_price, "ref_price")
        return ref * (1.0 - self.slippage_pct)

    def fee_for_fill(self, fill_price: float, size: float) -> float:
        """Per-side fee on the post-slippage fill notional: fill * size * fee_pct."""
        fill = _require_price(fill_price, "fill_price")
        amount = _require_size(size, "size")
        return fill * amount * self.fee_pct

    def funding_for_hold(self, notional: float, hold_ms: int) -> float:
        """Funding cost for holding `notional` over `hold_ms` milliseconds.

        Returns 0.0 when the funding rate is 0.0 (the default: disabled).
        The return is always a non-negative cost under the long-only,
        non-negative-rate convention documented above.
        """
        if isinstance(notional, bool) or not isinstance(notional, (int, float)):
            raise TypeError(
                f"notional must be a number, got {type(notional).__name__}."
            )
        nominal = float(notional)
        if not math.isfinite(nominal):
            raise ValueError(f"notional must be finite, got {notional!r}.")
        if nominal < 0.0:
            raise ValueError(f"notional must be >= 0, got {nominal}.")
        if isinstance(hold_ms, bool) or not isinstance(hold_ms, int):
            raise TypeError(f"hold_ms must be an int, got {type(hold_ms).__name__}.")
        if hold_ms < 0:
            raise ValueError(f"hold_ms must be >= 0, got {hold_ms}.")
        return nominal * self.funding_rate_per_hour * (hold_ms / 3_600_000)
