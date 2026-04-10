# =============================================================================
# Validation Service Tests
# =============================================================================
# Tests for price reconciliation and counterparty validation.
# =============================================================================

import pytest
from unittest.mock import MagicMock
from app.services.validation import (
    validate_fill_prices,
    validate_counterparty_quantities,
    validate_counterparty_completeness,
    ValidationError,
)


def _mock_leg(leg_index, side, volume, strike=96.0, option_type="C", pkg_prem=0.04):
    """Create a mock OrderLeg."""
    leg = MagicMock()
    leg.leg_index = leg_index
    leg.side = side
    leg.volume = volume
    leg.strike = strike
    leg.option_type = option_type
    leg.package_premium = pkg_prem
    return leg


def _mock_leg_price(leg_index, price):
    """Create a mock FillLegPrice."""
    lp = MagicMock()
    lp.leg_index = leg_index
    lp.price = price
    return lp


class TestPriceReconciliation:
    """Test fill price validation."""

    def test_valid_call_spread_prices(self):
        """Buy 96.00C at 0.07, sell 96.25C at 0.03 → net = 0.04."""
        order = MagicMock()
        order.legs = [
            _mock_leg(0, "B", 500, 96.00, "C", 0.04),
            _mock_leg(1, "S", 500, 96.25, "C", 0.04),
        ]
        fill = MagicMock()
        leg_prices = [
            _mock_leg_price(0, 0.07),
            _mock_leg_price(1, 0.03),
        ]
        # Should not raise
        validate_fill_prices(order, fill, leg_prices)

    def test_invalid_prices_raise(self):
        """Prices that don't reconcile should raise ValidationError."""
        order = MagicMock()
        order.legs = [
            _mock_leg(0, "B", 500, 96.00, "C", 0.04),
            _mock_leg(1, "S", 500, 96.25, "C", 0.04),
        ]
        fill = MagicMock()
        leg_prices = [
            _mock_leg_price(0, 0.07),
            _mock_leg_price(1, 0.02),  # Net = 0.05, expected 0.04
        ]
        with pytest.raises(ValidationError, match="reconciliation failed"):
            validate_fill_prices(order, fill, leg_prices)

    def test_futures_legs_skipped(self):
        """Futures legs (no strike, no option type) are excluded."""
        order = MagicMock()
        order.legs = [
            _mock_leg(0, "B", 500, 96.00, "C", 0.04),
            _mock_leg(1, "S", 200, None, None, 0.0),  # Futures
        ]
        order.legs[1].strike = None
        order.legs[1].option_type = None
        fill = MagicMock()
        leg_prices = [_mock_leg_price(0, 0.04)]
        # Should not raise (single option, net = premium)
        validate_fill_prices(order, fill, leg_prices)


class TestCounterpartyQuantities:
    """Test counterparty quantity validation."""

    def test_matching_quantities(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        cps = [MagicMock(quantity=300), MagicMock(quantity=200)]
        # Should not raise
        validate_counterparty_quantities(fill, cps)

    def test_mismatched_quantities_raise(self):
        fill = MagicMock()
        fill.fill_quantity = 500
        cps = [MagicMock(quantity=300), MagicMock(quantity=100)]
        with pytest.raises(ValidationError, match="does not match"):
            validate_counterparty_quantities(fill, cps)


class TestCounterpartyCompleteness:
    """Test counterparty field completeness."""

    def test_complete_counterparty(self):
        cp = MagicMock(quantity=500, broker="GFI", symbol="CITADEL", bracket="A")
        validate_counterparty_completeness([cp])

    def test_missing_broker_raises(self):
        cp = MagicMock(quantity=500, broker="", symbol="CITADEL", bracket="A")
        cp.broker = ""
        with pytest.raises(ValidationError, match="Broker"):
            validate_counterparty_completeness([cp])
