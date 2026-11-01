"""Pure Decimal financial helpers.

Scope: this module defines helpers and exact arithmetic ONLY. No
production call site uses it yet — migration order is order formatting
first, then risk sizing, then persistence, each in its own reviewed
subphase. Importing this module changes nothing about runtime behavior.

Rules enforced here:
- Addition, subtraction, and multiplication are exact provided the
  result fits within the ambient Decimal context precision (28
  significant digits by default — the caller's context applies, this
  module never mutates it). Larger intermediate results, and division
  with non-terminating results, are rounded by the context: Decimal
  arithmetic is NOT infinitely precise. Rounding that the caller can
  see and control happens solely at quantize_down / format_amount.
- ROUND_DOWN means toward zero (Decimal semantics), NOT mathematical
  floor: quantize_down(-0.001299, 5) == Decimal("-0.00129"), whereas a
  floor-based helper yields -0.00130. See tests/test_money.py for the
  comparison against utils.round_size().
- Floats are never silently accepted: parse_amount() rejects them with
  TypeError. from_float() is the single sanctioned bridge and uses
  Decimal(repr(value)) — the float's shortest round-trip decimal form,
  NOT the exact underlying binary value (Decimal(0.1) via repr is
  Decimal("0.1"); the true binary expansion is longer).
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation, ROUND_DOWN


def _require_decimal(value: object, name: str) -> Decimal:
    """Narrows an arbitrary object to a finite Decimal.

    Raises TypeError for anything that is not a Decimal instance
    (bool included — it is not treated as 0/1 here). Raises ValueError
    for non-finite Decimals (NaN, Infinity, -Infinity): silent NaN
    propagation and signaling-comparison surprises are worse than a
    loud rejection at the boundary.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise TypeError(
            f"{name} must be a Decimal, got {type(value).__name__}. "
            "Convert explicitly with parse_amount() or from_float()."
        )
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}.")
    return value


def parse_amount(value: str | int) -> Decimal:
    """Parses an exact Decimal amount from an API string or an int.

    Accepts: str (plain or scientific notation, e.g. "13109.48", "1E-7"),
    int (exact). Surrounding whitespace is stripped.
    Rejects with TypeError: float, bool, None, and any other type —
    floats must go through from_float() so the crossing is explicit.
    Rejects with ValueError: "", whitespace-only, malformed text
    (e.g. "1,000.0"), NaN, and positive/negative Infinity.
    """
    if isinstance(value, bool):
        raise TypeError(
            "parse_amount() does not accept bool; pass an explicit str or int."
        )
    if isinstance(value, int):
        return Decimal(value)
    if not isinstance(value, str):
        raise TypeError(
            f"parse_amount() requires str or int, got {type(value).__name__}."
        )
    text = value.strip()
    if not text:
        raise ValueError("parse_amount() received an empty string.")
    try:
        result = Decimal(text)
    except InvalidOperation as e:
        raise ValueError(f"parse_amount() could not parse {value!r}.") from e
    if not result.is_finite():
        raise ValueError(f"parse_amount() rejects non-finite value {value!r}.")
    return result


def from_float(value: float) -> Decimal:
    """Bridges a float to Decimal via its shortest round-trip form.

    Uses Decimal(repr(value)): from_float(0.1) == Decimal("0.1"). This
    represents the decimal a human means when printing the float, NOT
    the exact binary expansion stored in memory. Use only for values
    that originate as floats (indicator outputs, config settings) —
    never for exchange strings, which must go through parse_amount().
    Rejects non-floats with TypeError and NaN/Infinity with ValueError.
    """
    if isinstance(value, bool) or not isinstance(value, float):
        raise TypeError(f"from_float() requires a float, got {type(value).__name__}.")
    if not math.isfinite(value):
        raise ValueError(f"from_float() rejects non-finite value {value!r}.")
    return Decimal(repr(value))


def notional(price: Decimal, size: Decimal) -> Decimal:
    """Exact notional value: price * size (no rounding beyond ambient
    context precision — see module docstring)."""
    _require_decimal(price, "price")
    _require_decimal(size, "size")
    return price * size


def pnl_long(entry: Decimal, exit: Decimal, size: Decimal) -> Decimal:
    """Exact long-trade PnL: (exit - entry) * size (no rounding beyond
    ambient context precision — see module docstring)."""
    _require_decimal(entry, "entry")
    _require_decimal(exit, "exit")
    _require_decimal(size, "size")
    return (exit - entry) * size


def pnl_short(entry: Decimal, exit: Decimal, size: Decimal) -> Decimal:
    """Exact short-trade PnL: (entry - exit) * size (no rounding beyond
    ambient context precision — see module docstring)."""
    _require_decimal(entry, "entry")
    _require_decimal(exit, "exit")
    _require_decimal(size, "size")
    return (entry - exit) * size


def pnl_pct(entry: Decimal, exit: Decimal) -> Decimal:
    """Exact per-trade fractional return: (exit - entry) / entry.

    Unrounded (subject to ambient context precision for non-terminating
    results — see module docstring). Raises ValueError if entry <= 0.
    """
    _require_decimal(entry, "entry")
    _require_decimal(exit, "exit")
    if entry <= 0:
        raise ValueError(f"pnl_pct() requires entry > 0, got {entry}.")
    return (exit - entry) / entry


def quantize_down(value: Decimal, decimals: int) -> Decimal:
    """Truncates toward zero to `decimals` decimal places (ROUND_DOWN).

    This is truncation toward zero, NOT floor: negatives move toward
    zero (see module docstring). Matches exchange lot-size truncation
    semantics for non-negative sizes. Raises TypeError for non-int
    `decimals` (bool included) and ValueError for negative values.
    Raises ValueError (never silently rounds) when the value cannot be
    represented at `decimals` places within the ambient context
    precision — i.e. the quantized result would need more significant
    digits than the context supports.
    """
    _require_decimal(value, "value")
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        raise TypeError(f"decimals must be an int, got {type(decimals).__name__}.")
    if decimals < 0:
        raise ValueError(f"decimals must be >= 0, got {decimals}.")
    try:
        quantum = Decimal(1).scaleb(-decimals)
        return value.quantize(quantum, rounding=ROUND_DOWN)
    except InvalidOperation as e:
        raise ValueError(
            f"Cannot quantize {value} to {decimals} decimal places under "
            "the ambient Decimal context precision: the result would need "
            "more significant digits than the context supports. Reduce the "
            "magnitude or increase context precision at the call site."
        ) from e


def format_amount(value: Decimal, decimals: int) -> str:
    """Formats a Decimal as plain (non-exponent) notation for the wire.

    Truncates via quantize_down(), then renders with str.format 'f' so
    the output never contains exponent notation (e.g. "0.0000001", never
    "1E-7"). The result always re-parses through parse_amount() to the
    same value when `value` already fits within `decimals` places.
    Propagates quantize_down()'s documented ValueError unchanged.
    """
    return format(quantize_down(value, decimals), "f")
