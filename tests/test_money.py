"""tests for core.money — pure Decimal helpers.

No production call site uses these helpers yet; this file proves the
helpers in isolation. It changes no trading behavior and touches no
existing files.
"""

from __future__ import annotations

import math
from decimal import Decimal, getcontext

import pytest

from core.money import (
    format_amount,
    from_float,
    notional,
    parse_amount,
    pnl_long,
    pnl_pct,
    pnl_short,
    quantize_down,
)
from core.utils import round_size


# ──────────────────────────────────────────────────────────────
# parse_amount — valid inputs
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("13109.48", Decimal("13109.48")),  # accountValue shape
        ("0.0335", Decimal("0.0335")),  # szi shape
        ("-0.013", Decimal("-0.013")),  # negative pnl shape
        ("0.001", Decimal("0.001")),
        ("0", Decimal("0")),
        ("0.00000001", Decimal("0.00000001")),  # very small value
        ("1E-7", Decimal("0.0000001")),  # scientific notation, numeric ==
        ("-2.5e3", Decimal("-2500")),
        ("  12.50  ", Decimal("12.50")),  # surrounding whitespace stripped
    ],
)
def test_parse_amount_valid_strings(text: str, expected: Decimal) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize("value", [5, -3, 0, 10**18])
def test_parse_amount_valid_ints(value: int) -> None:
    assert parse_amount(value) == Decimal(value)


# ──────────────────────────────────────────────────────────────
# parse_amount — invalid inputs
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [0.1, 5.0, float("nan"), float("inf")])
def test_parse_amount_rejects_floats(value: object) -> None:
    """Floats must go through from_float() — never silently accepted."""
    with pytest.raises(TypeError):
        parse_amount(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, False, None, [1], {"a": 1}, (1, 2)])
def test_parse_amount_rejects_wrong_types(value: object) -> None:
    with pytest.raises(TypeError):
        parse_amount(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "abc",
        "12.34.56",
        "1,000.0",
        "--5",
        "NaN",
        "nan",
        "sNaN",
        "Infinity",
        "-Infinity",
        "inf",
        "+INF",
    ],
)
def test_parse_amount_rejects_bad_values(text: str) -> None:
    """Empty, malformed, NaN, and infinite inputs all raise ValueError."""
    with pytest.raises(ValueError):
        parse_amount(text)


# ──────────────────────────────────────────────────────────────
# from_float — explicit bridge
# ──────────────────────────────────────────────────────────────


def test_from_float_uses_shortest_round_trip_form() -> None:
    # Shortest round-trip decimal, NOT the exact binary expansion:
    assert from_float(0.1) == Decimal("0.1")
    assert from_float(0.1) != Decimal(0.1)  # Decimal(float) keeps binary error


def test_from_float_rejects_non_floats() -> None:
    for bad in ("0.1", 5, True, None):
        with pytest.raises(TypeError):
            from_float(bad)  # type: ignore[arg-type]


def test_from_float_rejects_non_finite() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            from_float(bad)


def test_decimal_accumulation_is_exact_where_float_drifts() -> None:
    """The canonical motivation: 0.07 summed 100x is exactly 7.0 in
    Decimal while binary float accumulation drifts to 7.000000000000001
    (verified on this platform; IEEE-754 double summation order is
    deterministic, so the drift is reproducible, not incidental).
    (Note: 0.1 summed 10x or 1000x happens to land exactly on 1.0 /
    100.0 here, so those classic cases do NOT demonstrate drift and are
    deliberately not used.)"""
    exact = sum((from_float(0.07) for _ in range(100)), Decimal(0))
    assert exact == Decimal("7.0")
    drifted = sum((0.07 for _ in range(100)), 0.0)
    assert drifted != 7.0
    assert math.isclose(drifted, 7.0, rel_tol=1e-9)  # close, but not exact


# ──────────────────────────────────────────────────────────────
# Exact arithmetic (mirrors the G1 golden numbers in Decimal)
# ──────────────────────────────────────────────────────────────


def test_notional_is_exact() -> None:
    assert notional(Decimal("50000"), Decimal("0.001")) == Decimal("50")
    assert notional(Decimal("0.1"), Decimal("0.1")) == Decimal("0.01")


def test_pnl_long_matches_g1_numbers_exactly() -> None:
    assert pnl_long(Decimal("151.5"), Decimal("247.5"), Decimal("2")) == Decimal(
        "192.0"
    )


def test_pnl_short_is_mirror_image() -> None:
    assert pnl_short(Decimal("151.5"), Decimal("247.5"), Decimal("2")) == Decimal(
        "-192.0"
    )
    assert pnl_short(Decimal("247.5"), Decimal("151.5"), Decimal("2")) == Decimal(
        "192.0"
    )


def test_pnl_pct_exact_and_guarded() -> None:
    # (247.5 - 151.5) / 151.5 == 96 / 151.5, computed independently here.
    assert pnl_pct(Decimal("151.5"), Decimal("247.5")) == Decimal(96) / Decimal("151.5")
    with pytest.raises(ValueError):
        pnl_pct(Decimal("0"), Decimal("100"))
    with pytest.raises(ValueError):
        pnl_pct(Decimal("-5"), Decimal("100"))


def test_arithmetic_rejects_non_decimal() -> None:
    with pytest.raises(TypeError):
        notional(50000.0, Decimal("0.001"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        pnl_long(Decimal("1"), Decimal("2"), 3.0)  # type: ignore[arg-type]


NON_FINITE_DECIMALS = [
    Decimal("NaN"),
    Decimal("sNaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
]


def test_arithmetic_rejects_non_finite_decimals() -> None:
    """NaN (quiet or signaling) and infinities are rejected with
    ValueError by every arithmetic helper — no silent NaN propagation,
    no raw InvalidOperation from signaling comparisons."""
    for bad in NON_FINITE_DECIMALS:
        with pytest.raises(ValueError):
            notional(bad, Decimal("1"))
        with pytest.raises(ValueError):
            notional(Decimal("1"), bad)
        with pytest.raises(ValueError):
            pnl_long(bad, Decimal("2"), Decimal("1"))
        with pytest.raises(ValueError):
            pnl_long(Decimal("1"), bad, Decimal("1"))
        with pytest.raises(ValueError):
            pnl_short(bad, Decimal("2"), Decimal("1"))
        with pytest.raises(ValueError):
            pnl_short(Decimal("1"), bad, Decimal("1"))


def test_pnl_pct_rejects_non_finite_decimals() -> None:
    for bad in NON_FINITE_DECIMALS:
        with pytest.raises(ValueError):
            pnl_pct(bad, Decimal("100"))
        with pytest.raises(ValueError):
            pnl_pct(Decimal("100"), bad)


def test_quantize_and_format_reject_non_finite_decimals() -> None:
    for bad in NON_FINITE_DECIMALS:
        with pytest.raises(ValueError):
            quantize_down(bad, 2)
        with pytest.raises(ValueError):
            format_amount(bad, 2)


# ──────────────────────────────────────────────────────────────
# Division demonstrates context precision (bounded, not infinite)
# ──────────────────────────────────────────────────────────────


def test_division_is_bounded_by_context_precision() -> None:
    """Decimal division is NOT infinitely precise: 1/3 rounds to the
    ambient 28-digit context. Arithmetic is exact; division is exact
    only up to context precision — which is why rounding happens
    solely at quantize/format boundaries."""
    third = Decimal(1) / Decimal(3)
    assert str(third) == "0." + "3" * 28
    assert third * 3 != 1  # rounded result cannot reconstruct 1 exactly
    assert getcontext().prec == 28  # module must not mutate global context


# ──────────────────────────────────────────────────────────────
# quantize_down — toward zero, not floor
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (Decimal("0.00123456"), 5, Decimal("0.00123")),
        (Decimal("0.123456"), 3, Decimal("0.123")),
        (Decimal("1.999999"), 2, Decimal("1.99")),
        (Decimal("100.456789"), 2, Decimal("100.45")),
        (Decimal("5"), 0, Decimal("5")),
        (Decimal("0"), 2, Decimal("0")),
        # Negatives truncate TOWARD ZERO (Decimal ROUND_DOWN):
        (Decimal("-0.001299"), 5, Decimal("-0.00129")),
        (Decimal("-1.999"), 2, Decimal("-1.99")),
        (Decimal("-0.5"), 0, Decimal("-0")),
    ],
)
def test_quantize_down_vectors(
    value: Decimal, decimals: int, expected: Decimal
) -> None:
    assert quantize_down(value, decimals) == expected


@pytest.mark.parametrize("decimals", [-1, -100])
def test_quantize_down_rejects_negative_decimals(decimals: int) -> None:
    with pytest.raises(ValueError):
        quantize_down(Decimal("1.5"), decimals)


@pytest.mark.parametrize("decimals", [2.0, "2", None, True, False])
def test_quantize_down_rejects_non_int_decimals(decimals: object) -> None:
    with pytest.raises(TypeError):
        quantize_down(Decimal("1.5"), decimals)  # type: ignore[arg-type]


def test_quantize_down_rejects_non_decimal_value() -> None:
    with pytest.raises(TypeError):
        quantize_down(1.5, 2)  # type: ignore[arg-type]


def test_quantize_down_rejects_oversized_values() -> None:
    """A finite value needing more significant digits than the ambient
    28-digit context supports cannot be quantized: documented
    ValueError (never silent rounding, never a raw InvalidOperation,
    never a context change to hide it). format_amount() inherits it."""
    huge = Decimal("123456789012345678901234567890.12")  # 32 sig. digits
    with pytest.raises(ValueError, match="context precision"):
        quantize_down(huge, 2)
    with pytest.raises(ValueError, match="context precision"):
        format_amount(huge, 2)


def test_quantize_down_accepts_largest_fitting_values() -> None:
    """The boundary counterpart: 28 significant digits still quantize
    fine, proving the rejection above is about context capacity, not
    magnitude alone."""
    fitting = Decimal("12345678901234567890123456.12")  # exactly 28 sig. digits
    assert quantize_down(fitting, 2) == fitting


def test_quantize_down_vs_round_size_documents_difference() -> None:
    """Benign positive vectors agree with utils.round_size(); negatives
    do NOT: ROUND_DOWN truncates toward zero while math.floor-based
    round_size() moves away from zero. Both behaviors are correct for
    their own contracts — this test documents the difference so a future
    migration picks the right one deliberately."""
    benign = [
        (0.00123456, 5, "0.00123"),
        (0.123456, 3, "0.123"),
        (1.999999, 2, "1.99"),
        (100.456789, 2, "100.45"),
    ]
    for raw, decimals, expected in benign:
        assert str(quantize_down(Decimal(str(raw)), decimals)) == expected
        assert str(round_size(raw, decimals)) == expected
    # Documented divergence on negatives (toward-zero vs floor):
    assert str(quantize_down(Decimal("-0.001299"), 5)) == "-0.00129"
    assert str(round_size(-0.001299, 5)) == "-0.0013"
    assert quantize_down(Decimal("-0.001299"), 5) != Decimal(
        str(round_size(-0.001299, 5))
    )


# ──────────────────────────────────────────────────────────────
# format_amount — plain notation wire format
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (Decimal("50000"), 2, "50000.00"),
        (Decimal("-0.013"), 4, "-0.0130"),
        (Decimal("0"), 2, "0.00"),
        (Decimal("0.0000001"), 7, "0.0000001"),
        (Decimal("1E-7"), 7, "0.0000001"),  # never exponent notation
        (Decimal("1E+7"), 0, "10000000"),  # never exponent notation
        (Decimal("0.00123456"), 5, "0.00123"),  # truncates, toward zero
        (Decimal("-0.001299"), 5, "-0.00129"),
    ],
)
def test_format_amount_vectors(value: Decimal, decimals: int, expected: str) -> None:
    out = format_amount(value, decimals)
    assert out == expected
    assert "E" not in out and "e" not in out


def test_format_amount_round_trips_through_parse_amount() -> None:
    for text, decimals in [
        ("13109.48", 2),
        ("0.0335", 4),
        ("10000000", 0),
        ("-0.013", 4),
    ]:
        original = parse_amount(text)
        assert parse_amount(format_amount(original, decimals)) == original
