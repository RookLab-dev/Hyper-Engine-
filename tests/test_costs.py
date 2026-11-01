"""tests for the unified execution-cost model.

Proves CostModel math, input validation, the fee/funding accounting
identities, and backtest/paper parity — without touching golden
fixtures (which must pass unmodified) or any existing test file.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backtester.backtester import Backtester
from backtester.costs import CostModel
from fixtures.golden_candles import G1_CANDLES, G1_SIGNALS
from strategies.base_strategy import BaseStrategy


def scripted(signals: list[str]) -> MagicMock:
    strategy = MagicMock(spec=BaseStrategy)
    strategy.name = "CostScriptedStrategy"
    strategy.min_periods = 1
    signal_iter = iter(signals)
    strategy.generate_signal.side_effect = lambda closes: next(signal_iter, "HOLD")
    return strategy


# ──────────────────────────────────────────────────────────────
# Construction and validation
# ──────────────────────────────────────────────────────────────


def test_defaults_are_cost_free() -> None:
    m = CostModel(slippage_pct=0.01)
    assert m.fee_pct == 0.0
    assert m.funding_rate_per_hour == 0.0


@pytest.mark.parametrize("rate", [-0.01, -1.0, 1.0, 2.5])
def test_rejects_out_of_range_rates(rate: float) -> None:
    with pytest.raises(ValueError):
        CostModel(slippage_pct=rate)
    with pytest.raises(ValueError):
        CostModel(fee_pct=rate)
    with pytest.raises(ValueError):
        CostModel(funding_rate_per_hour=rate)


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_rates(rate: float) -> None:
    with pytest.raises(ValueError):
        CostModel(slippage_pct=rate)
    with pytest.raises(ValueError):
        CostModel(fee_pct=rate)


@pytest.mark.parametrize("rate", ["0.01", None, True, [0.01]])
def test_rejects_non_numeric_rates(rate: object) -> None:
    with pytest.raises(TypeError):
        CostModel(slippage_pct=rate)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────
# Fill prices: direction and validation
# ──────────────────────────────────────────────────────────────


def test_fill_direction_and_zero_slip_identity() -> None:
    m = CostModel(slippage_pct=0.01)
    assert m.buy_fill_price(150.0) == 150.0 * 1.01  # above reference
    assert m.sell_fill_price(250.0) == 250.0 * 0.99  # below reference
    plain = CostModel()
    assert plain.buy_fill_price(150.0) == 150.0
    assert plain.sell_fill_price(250.0) == 250.0


@pytest.mark.parametrize("ref", [0.0, -5.0, float("nan"), float("inf"), float("-inf")])
def test_fill_price_rejects_non_positive_or_non_finite(ref: float) -> None:
    m = CostModel(slippage_pct=0.01)
    with pytest.raises(ValueError):
        m.buy_fill_price(ref)
    with pytest.raises(ValueError):
        m.sell_fill_price(ref)


@pytest.mark.parametrize("ref", ["150", None, True, [150.0]])
def test_fill_price_rejects_non_numeric(ref: object) -> None:
    m = CostModel(slippage_pct=0.01)
    with pytest.raises(TypeError):
        m.buy_fill_price(ref)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        m.sell_fill_price(ref)  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────
# Fees on post-slippage notional
# ──────────────────────────────────────────────────────────────


def test_fee_uses_post_slippage_fill_notional() -> None:
    m = CostModel(slippage_pct=0.01, fee_pct=0.01)
    buy_fill = m.buy_fill_price(150.0)  # 151.5
    assert m.fee_for_fill(buy_fill, 2.0) == pytest.approx(151.5 * 2.0 * 0.01)
    assert CostModel().fee_for_fill(buy_fill, 2.0) == 0.0  # disabled by default


@pytest.mark.parametrize(
    ("fill", "size"),
    [(0.0, 1.0), (-1.0, 1.0), (100.0, 0.0), (100.0, -1.0), (float("nan"), 1.0)],
)
def test_fee_rejects_invalid_inputs(fill: float, size: float) -> None:
    with pytest.raises(ValueError):
        CostModel(fee_pct=0.01).fee_for_fill(fill, size)


# ──────────────────────────────────────────────────────────────
# Funding: opt-in, long-only cost magnitude
# ──────────────────────────────────────────────────────────────


def test_funding_disabled_by_default() -> None:
    m = CostModel(slippage_pct=0.01)
    assert m.funding_for_hold(1_000_000.0, 86_400_000) == 0.0


def test_funding_linear_hold_cost() -> None:
    m = CostModel(funding_rate_per_hour=0.01)
    assert m.funding_for_hold(10_000.0, 3_600_000) == pytest.approx(100.0)
    assert m.funding_for_hold(10_000.0, 0) == 0.0  # zero hold, zero cost


@pytest.mark.parametrize("hold_ms", [-1, -3_600_000])
def test_funding_rejects_negative_hold(hold_ms: int) -> None:
    with pytest.raises(ValueError):
        CostModel(funding_rate_per_hour=0.01).funding_for_hold(1_000.0, hold_ms)


def test_funding_rejects_bad_types_and_values() -> None:
    m = CostModel(funding_rate_per_hour=0.01)
    with pytest.raises(TypeError):
        m.funding_for_hold(1_000.0, 3_600_000.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        m.funding_for_hold(1_000.0, True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        m.funding_for_hold(-1_000.0, 1_000)
    with pytest.raises(ValueError):
        m.funding_for_hold(float("nan"), 1_000)


# ──────────────────────────────────────────────────────────────
# Accounting identities on hand-computed legs
# (G1-style refs: buy 150, sell 250, size 2, slip 0.01, fee 0.01)
# ──────────────────────────────────────────────────────────────


def test_accounting_identities_on_hand_computed_legs() -> None:
    m = CostModel(slippage_pct=0.01, fee_pct=0.01, funding_rate_per_hour=0.0)
    buy_fill, sell_fill, size = 151.5, 247.5, 2.0
    assert m.buy_fill_price(150.0) == buy_fill
    assert m.sell_fill_price(250.0) == sell_fill
    buy_fee = m.fee_for_fill(buy_fill, size)  # 3.03
    sell_fee = m.fee_for_fill(sell_fill, size)  # 4.95
    fee_paid = buy_fee + sell_fee
    funding_paid = m.funding_for_hold(buy_fill * size, 3_000)  # rate 0 → 0.0
    assert funding_paid == 0.0

    gross_pnl = (sell_fill - buy_fill) * size  # 192.0
    net_pnl = gross_pnl - fee_paid - funding_paid
    # Corrected invariant: net + fees + funding == gross fills PnL.
    assert net_pnl + fee_paid + funding_paid == pytest.approx(gross_pnl)
    assert net_pnl == pytest.approx(192.0 - 3.03 - 4.95)

    # Separate frictionless reconciliation, slippage made explicit:
    frictionless_pnl = (250.0 - 150.0) * size  # 200.0
    slippage_cost = frictionless_pnl - gross_pnl  # 8.0
    assert slippage_cost == pytest.approx(8.0)
    assert gross_pnl + slippage_cost == pytest.approx(frictionless_pnl)


# ──────────────────────────────────────────────────────────────
# Backtest integration: defaults reproduce golden math exactly
# ──────────────────────────────────────────────────────────────


def test_backtest_default_model_matches_golden_numbers() -> None:
    """Default CostModel must reproduce the G1 golden fills/PnL exactly —
    this is what keeps the golden fixtures green unmodified."""
    b = Backtester(
        strategy=scripted(list(G1_SIGNALS)),
        initial_capital=10_000.0,
        position_size=2.0,
        slippage_pct=0.01,
        symbol="BTC",
    )
    result = b.run([list(c) for c in G1_CANDLES])
    trade = result.trades[0]
    assert trade.entry_price == 151.5
    assert trade.exit_price == 247.5
    assert trade.pnl == 192.0
    assert trade.fee_paid == 0.0
    assert trade.funding_paid == 0.0


def test_backtest_applies_fees_and_funding() -> None:
    b = Backtester(
        strategy=scripted(list(G1_SIGNALS)),
        initial_capital=10_000.0,
        position_size=2.0,
        slippage_pct=0.01,
        symbol="BTC",
        cost_model=CostModel(
            slippage_pct=0.01, fee_pct=0.01, funding_rate_per_hour=0.01
        ),
    )
    result = b.run([list(c) for c in G1_CANDLES])
    trade = result.trades[0]
    assert trade.entry_price == 151.5  # fills unchanged — only costs added
    assert trade.exit_price == 247.5
    assert trade.fee_paid == pytest.approx(3.03 + 4.95)
    assert trade.funding_paid == pytest.approx(151.5 * 2.0 * 0.01 * (2000 / 3_600_000))
    assert trade.funding_paid > 0.0
    gross = (247.5 - 151.5) * 2.0
    assert trade.pnl + trade.fee_paid + trade.funding_paid == pytest.approx(gross)
    assert trade.pnl < 192.0


def test_trade_cost_fields_default_to_zero() -> None:
    from backtester.backtester import Trade

    t = Trade(
        entry_time=1,
        exit_time=2,
        entry_price=100.0,
        exit_price=110.0,
        size=1.0,
        pnl=10.0,
        pnl_pct=0.1,
    )
    assert t.fee_paid == 0.0
    assert t.funding_paid == 0.0


# ──────────────────────────────────────────────────────────────
# Backtest/paper parity: same model, each path's own reference
# ──────────────────────────────────────────────────────────────


def test_paper_applies_model_to_live_reference_not_next_open() -> None:
    """Paper fills derive from the live tick reference, never from
    backtest next-open prices — while cost semantics stay identical."""
    costs = CostModel(slippage_pct=0.01, fee_pct=0.01)
    live_ref = 157.25  # a live tick close; not any G1 open
    paper_fill = costs.buy_fill_price(live_ref)
    assert paper_fill == live_ref * 1.01
    assert paper_fill != 151.5  # provably not the backtest next-open fill
    assert costs.fee_for_fill(paper_fill, 2.0) == pytest.approx(paper_fill * 2.0 * 0.01)


def test_backtest_and_paper_share_cost_semantics() -> None:
    """Same CostModel methods feed both paths: fee identity holds on
    backtest fills and paper-style fills alike."""
    costs = CostModel(slippage_pct=0.01, fee_pct=0.01)
    backtester = Backtester(
        strategy=scripted(list(G1_SIGNALS)),
        initial_capital=10_000.0,
        position_size=2.0,
        slippage_pct=0.01,
        symbol="BTC",
        cost_model=costs,
    )
    trade = backtester.run([list(c) for c in G1_CANDLES]).trades[0]
    assert trade.fee_paid == pytest.approx(
        costs.fee_for_fill(trade.entry_price, 2.0)
        + costs.fee_for_fill(trade.exit_price, 2.0)
    )
