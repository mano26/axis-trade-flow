# =============================================================================
# Validation Edge Case Tests — designed to break things
# =============================================================================
# Targets the price reconciliation algorithm, counterparty validation,
# and the pre-generation gate in validation.py.
#
# Run with: pytest tests/test_validation_edge_cases.py -v
# =============================================================================

import pytest
from unittest.mock import MagicMock, PropertyMock
from app.services.validation import (
    validate_fill_prices,
    validate_counterparty_quantities,
    validate_counterparty_completeness,
    validate_before_generate,
    ValidationError,
    PRICE_TOLERANCE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _leg(leg_index, side, volume, strike=96.0, option_type="C", pkg_prem=0.04):
    m = MagicMock()
    m.leg_index = leg_index
    m.side = side
    m.volume = volume
    m.strike = strike
    m.option_type = option_type
    m.package_premium = pkg_prem
    return m


def _fut_leg(leg_index, volume, pkg_prem=0.0):
    """Futures leg — strike and option_type are None."""
    m = MagicMock()
    m.leg_index = leg_index
    m.side = "S"
    m.volume = volume
    m.strike = None
    m.option_type = None
    m.package_premium = pkg_prem
    return m


def _lp(leg_index, price):
    m = MagicMock()
    m.leg_index = leg_index
    m.price = price
    return m


def _cp(qty, broker="GFI", symbol="CITADEL", bracket="A"):
    m = MagicMock()
    m.quantity = qty
    m.broker = broker
    m.symbol = symbol
    m.bracket = bracket
    return m


# ---------------------------------------------------------------------------
# Price Reconciliation — Valid Cases
# ---------------------------------------------------------------------------

class TestPriceReconciliationValid:

    def test_single_option_buy(self):
        """Single buy leg: net = –1 * 1 * price; |net| must equal premium."""
        order = MagicMock()
        order.legs = [_leg(0, "B", 500, pkg_prem=0.04)]
        validate_fill_prices(order, MagicMock(), [_lp(0, 0.04)])  # should not raise

    def test_single_option_sell(self):
        order = MagicMock()
        order.legs = [_leg(0, "S", 500, pkg_prem=0.04)]
        validate_fill_prices(order, MagicMock(), [_lp(0, 0.04)])

    def test_call_spread_exact(self):
        """Buy 96.00C at 0.07, sell 96.25C at 0.03 → net = 0.04."""
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, 96.00, "C", 0.04),
            _leg(1, "S", 500, 96.25, "C", 0.04),
        ]
        validate_fill_prices(order, MagicMock(), [_lp(0, 0.07), _lp(1, 0.03)])

    def test_butterfly_default_ratios(self):
        """1:2:1 butterfly. B 0.05, S 0.04, B 0.01 → net = 0.05 + 0.01 – 2*0.04 = –0.02 → |net|=0.02."""
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, 96.00, "C", 0.02),
            _leg(1, "S", 1000, 96.25, "C", 0.02),
            _leg(2, "B", 500, 96.50, "C", 0.02),
        ]
        # net = -1*(500/500)*0.05 + 1*(1000/500)*0.04 + -1*(500/500)*0.01
        # = -0.05 + 0.08 - 0.01 = 0.02 → |net| = 0.02 ✓
        validate_fill_prices(order, MagicMock(),
                             [_lp(0, 0.05), _lp(1, 0.04), _lp(2, 0.01)])

    def test_zero_premium_even_money(self):
        """Even-money spread — premium = 0, legs must net to zero."""
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, 96.00, "C", 0.0),
            _leg(1, "S", 500, 96.25, "C", 0.0),
        ]
        validate_fill_prices(order, MagicMock(), [_lp(0, 0.04), _lp(1, 0.04)])

    def test_futures_legs_skipped(self):
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, 96.00, "C", 0.04),
            _fut_leg(1, 200),
        ]
        validate_fill_prices(order, MagicMock(), [_lp(0, 0.04)])

    def test_vs_trade_two_premium_groups(self):
        """VS trade: two separate package premiums, each reconciled independently."""
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, 96.00, "C", 0.04),
            _leg(1, "S", 500, 96.25, "C", 0.03),
        ]
        leg_prices = [_lp(0, 0.04), _lp(1, 0.03)]
        validate_fill_prices(order, MagicMock(), leg_prices)

    def test_tolerance_boundary_just_inside(self):
        """Value within tolerance should pass."""
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, pkg_prem=0.04),
            _leg(1, "S", 500, pkg_prem=0.04),
        ]
        epsilon = PRICE_TOLERANCE * 0.5
        validate_fill_prices(
            order, MagicMock(),
            [_lp(0, 0.07 + epsilon), _lp(1, 0.03)]
        )


# ---------------------------------------------------------------------------
# Price Reconciliation — Invalid Cases
# ---------------------------------------------------------------------------

class TestPriceReconciliationInvalid:

    def test_spread_prices_dont_reconcile(self):
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, 96.00, "C", 0.04),
            _leg(1, "S", 500, 96.25, "C", 0.04),
        ]
        with pytest.raises(ValidationError, match="reconciliation failed"):
            validate_fill_prices(order, MagicMock(),
                                 [_lp(0, 0.07), _lp(1, 0.02)])  # net=0.05, exp=0.04

    def test_missing_leg_price_reported(self):
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, pkg_prem=0.04),
            _leg(1, "S", 500, pkg_prem=0.04),
        ]
        with pytest.raises(ValidationError, match="Missing price for leg 1"):
            validate_fill_prices(order, MagicMock(), [_lp(0, 0.07)])

    def test_tolerance_boundary_just_outside(self):
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, pkg_prem=0.04),
            _leg(1, "S", 500, pkg_prem=0.04),
        ]
        epsilon = PRICE_TOLERANCE * 2
        with pytest.raises(ValidationError, match="reconciliation"):
            validate_fill_prices(
                order, MagicMock(),
                [_lp(0, 0.07 + epsilon), _lp(1, 0.03)]
            )

    def test_butterfly_ratio_mismatch_caught(self):
        """1:2:1 butterfly — user enters prices that net to 0 instead of pkg_prem=0.02.
        net = -1*(500/500)*0.03 + 1*(1000/500)*0.02 + -1*(500/500)*0.01 = 0.00 ≠ 0.02."""
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, 96.00, "C", 0.02),
            _leg(1, "S", 1000, 96.25, "C", 0.02),
            _leg(2, "B", 500, 96.50, "C", 0.02),
        ]
        with pytest.raises(ValidationError):
            validate_fill_prices(order, MagicMock(),
                                 [_lp(0, 0.03), _lp(1, 0.02), _lp(2, 0.01)])

    def test_single_option_wrong_price(self):
        order = MagicMock()
        order.legs = [_leg(0, "B", 500, pkg_prem=0.04)]
        with pytest.raises(ValidationError):
            validate_fill_prices(order, MagicMock(), [_lp(0, 0.05)])

    def test_error_message_contains_expected_and_actual(self):
        order = MagicMock()
        order.legs = [
            _leg(0, "B", 500, pkg_prem=0.04),
            _leg(1, "S", 500, pkg_prem=0.04),
        ]
        try:
            validate_fill_prices(order, MagicMock(),
                                 [_lp(0, 0.08), _lp(1, 0.02)])  # net=0.06
        except ValidationError as e:
            assert "0.04" in str(e) or "0.0400" in str(e)
            assert "0.06" in str(e) or "0.0600" in str(e)


# ---------------------------------------------------------------------------
# Counterparty Quantity Validation
# ---------------------------------------------------------------------------

class TestCounterpartyQuantities:

    def test_exact_match(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        validate_counterparty_quantities(fill, [_cp(300), _cp(200)])

    def test_single_counterparty_exact(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        validate_counterparty_quantities(fill, [_cp(500)])

    def test_over_allocation_raises(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        with pytest.raises(ValidationError, match="does not match"):
            validate_counterparty_quantities(fill, [_cp(300), _cp(300)])

    def test_under_allocation_raises(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        with pytest.raises(ValidationError):
            validate_counterparty_quantities(fill, [_cp(200)])

    def test_empty_counterparty_list_raises(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        with pytest.raises(ValidationError):
            validate_counterparty_quantities(fill, [])

    def test_three_counterparties_sum_correct(self):
        fill = MagicMock()
        fill.fill_quantity = 600
        validate_counterparty_quantities(fill, [_cp(200), _cp(200), _cp(200)])

    def test_error_message_shows_fill_and_total(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        try:
            validate_counterparty_quantities(fill, [_cp(400)])
        except ValidationError as e:
            assert "500" in str(e)
            assert "400" in str(e)


# ---------------------------------------------------------------------------
# Counterparty Completeness
# ---------------------------------------------------------------------------

class TestCounterpartyCompleteness:

    def test_all_fields_present(self):
        validate_counterparty_completeness([_cp(500, "GFI", "CITADEL", "A")])

    def test_missing_broker_raises(self):
        cp = _cp(500, broker="")
        with pytest.raises(ValidationError, match="Broker"):
            validate_counterparty_completeness([cp])

    def test_whitespace_only_broker_raises(self):
        cp = _cp(500, broker="   ")
        with pytest.raises(ValidationError, match="Broker"):
            validate_counterparty_completeness([cp])

    def test_missing_symbol_raises(self):
        cp = _cp(500, symbol="")
        with pytest.raises(ValidationError, match="Symbol"):
            validate_counterparty_completeness([cp])

    def test_missing_bracket_raises(self):
        cp = _cp(500, bracket="")
        with pytest.raises(ValidationError, match="Bracket"):
            validate_counterparty_completeness([cp])

    def test_zero_quantity_raises(self):
        cp = _cp(0)
        with pytest.raises(ValidationError, match="Qty"):
            validate_counterparty_completeness([cp])

    def test_negative_quantity_raises(self):
        cp = _cp(-100)
        with pytest.raises(ValidationError, match="Qty"):
            validate_counterparty_completeness([cp])

    def test_multiple_cps_one_invalid_reports_row(self):
        cps = [
            _cp(300, "GFI", "CITADEL", "A"),
            _cp(200, broker=""),  # row 2 missing broker
        ]
        with pytest.raises(ValidationError) as exc_info:
            validate_counterparty_completeness(cps)
        assert "row 2" in str(exc_info.value)

    def test_multiple_invalid_cps_all_reported(self):
        cps = [_cp(0), _cp(0)]
        with pytest.raises(ValidationError) as exc_info:
            validate_counterparty_completeness(cps)
        # Both rows should appear in the error list
        assert len(exc_info.value.errors) == 2


# ---------------------------------------------------------------------------
# Pre-Generation Gate
# ---------------------------------------------------------------------------

class TestValidateBeforeGenerate:

    def _build_valid_order(self):
        order = MagicMock()
        order.house = "GFI"
        order.account = "ACCT001"
        fill = MagicMock()
        fill.id = 1
        fill.allocation_status = "allocated"
        fill.leg_prices = [MagicMock()]
        order.fills = [fill]
        return order

    def test_valid_order_passes(self):
        validate_before_generate(self._build_valid_order())

    def test_no_fills_raises(self):
        order = self._build_valid_order()
        order.fills = []
        with pytest.raises(ValidationError, match="No fills"):
            validate_before_generate(order)

    def test_missing_house_raises(self):
        order = self._build_valid_order()
        order.house = ""
        with pytest.raises(ValidationError, match="House"):
            validate_before_generate(order)

    def test_none_house_raises(self):
        order = self._build_valid_order()
        order.house = None
        with pytest.raises(ValidationError, match="House"):
            validate_before_generate(order)

    def test_missing_account_raises(self):
        order = self._build_valid_order()
        order.account = ""
        with pytest.raises(ValidationError, match="Account"):
            validate_before_generate(order)

    def test_pending_allocation_raises(self):
        order = self._build_valid_order()
        order.fills[0].allocation_status = "pending_allocation"
        with pytest.raises(ValidationError, match="counterparty allocation"):
            validate_before_generate(order)

    def test_no_leg_prices_raises(self):
        order = self._build_valid_order()
        order.fills[0].leg_prices = []
        with pytest.raises(ValidationError, match="no leg prices"):
            validate_before_generate(order)

    def test_multiple_fills_one_unallocated_raises(self):
        order = self._build_valid_order()
        fill2 = MagicMock()
        fill2.id = 2
        fill2.allocation_status = "pending_allocation"
        fill2.leg_prices = [MagicMock()]
        order.fills = [order.fills[0], fill2]
        with pytest.raises(ValidationError):
            validate_before_generate(order)

    def test_all_errors_collected_before_raising(self):
        """Multiple failures should accumulate into a single ValidationError."""
        order = MagicMock()
        order.house = ""
        order.account = ""
        order.fills = []
        with pytest.raises(ValidationError) as exc_info:
            validate_before_generate(order)
        assert len(exc_info.value.errors) >= 2
