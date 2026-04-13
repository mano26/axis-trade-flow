# =============================================================================
# Strategy Handler Tests
# =============================================================================
# Tests that the strategy builders produce the correct leg structures.
# =============================================================================

import pytest
from app.services.trade_parser import TradeInput
from app.services.strategy_handlers import (
    build_legs, build_straddle, build_call_spread, build_call_butterfly,
    build_iron_condor, build_single_option, build_cvd_overlay,
    get_expiry, get_contract_type, get_card_mo_code,
)


class TestExpiryResolution:
    """Test contract code → expiry string conversion."""

    def test_quarterly_option(self):
        # SFRH6 = March of a year ending in 6. If that month is in the past,
        # the expiry bumps forward. Assert format and that the result is not in the past.
        from datetime import date
        result = get_expiry("SFRH6")
        assert result[:3] == "MAR"
        year = int(result[3:]) + 2000
        assert year >= date.today().year

    def test_quarterly_future(self):
        from datetime import date
        result = get_expiry("SFRH6", is_future=True)
        assert result[:3] == "MAR"
        year = int(result[3:]) + 2000
        assert year >= date.today().year

    def test_short_dated(self):
        from datetime import date
        result = get_expiry("0QZ5")
        assert result[:3] == "DEC"
        year = int(result[3:]) + 2000
        assert year >= date.today().year


class TestContractTypeResolution:
    """Test contract code → type code conversion."""

    def test_sfr_is_sr3(self):
        assert get_contract_type("SFRH6") == "SR3"

    def test_short_dated_0q_is_s0(self):
        assert get_contract_type("0QZ5") == "S0"

    def test_future_always_sr3(self):
        assert get_contract_type("0QZ5", is_future=True) == "SR3"


class TestBuildStraddle:
    """Test straddle leg builder."""

    def test_produces_two_legs(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[95.75],
            strategy="straddle",
            volume=100,
            direction_side="B",
        )
        legs = build_straddle(trade)
        assert len(legs) == 2
        assert legs[0]["option_type"] == "P"
        assert legs[1]["option_type"] == "C"
        assert legs[0]["strike"] == 95.75
        assert legs[1]["strike"] == 95.75


class TestBuildCallSpread:
    """Test call spread leg builder."""

    def test_buy_low_sell_high(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[96.00, 96.25],
            option_types=["C"],
            strategy="cs",
            volume=500,
            direction_side="B",
            is_call_centric=True,
        )
        legs = build_call_spread(trade)
        assert len(legs) == 2
        assert legs[0]["side"] == "B"
        assert legs[0]["strike"] == 96.00
        assert legs[1]["side"] == "S"
        assert legs[1]["strike"] == 96.25


class TestBuildCallButterfly:
    """Test call butterfly leg builder with default ratios."""

    def test_default_1x2x1_volumes(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[96.00, 96.25, 96.50],
            strategy="bflyc",
            volume=200,
            direction_side="B",
        )
        legs = build_legs(trade)
        assert len(legs) == 3
        assert legs[0]["volume"] == 200   # 1x base
        assert legs[1]["volume"] == 400   # 2x base (default ratio)
        assert legs[2]["volume"] == 200   # 1x base
        assert all(l["option_type"] == "C" for l in legs)

    def test_custom_1x3x2_volumes(self):
        trade = TradeInput(
            contract_codes=["SFRM6"],
            strikes=[96.25, 96.50, 96.625],
            strategy="bflyc",
            volume=500,
            direction_side="B",
            ratios=[1, 3, 2],
        )
        legs = build_legs(trade)
        assert len(legs) == 3
        assert legs[0]["volume"] == 500    # 1x500
        assert legs[1]["volume"] == 1500   # 3x500
        assert legs[2]["volume"] == 1000   # 2x500

    def test_custom_ratio_condor(self):
        trade = TradeInput(
            contract_codes=["SFRM6"],
            strikes=[96.25, 96.50, 96.75, 97.00],
            strategy="condorc",
            volume=1000,
            direction_side="B",
            ratios=[1, 2, 2, 2],
        )
        legs = build_legs(trade)
        assert len(legs) == 4
        assert legs[0]["volume"] == 1000
        assert legs[1]["volume"] == 2000
        assert legs[2]["volume"] == 2000
        assert legs[3]["volume"] == 2000

    def test_custom_ratio_straddle(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[96.25],
            strategy="straddle",
            volume=500,
            direction_side="B",
            ratios=[1, 2],
        )
        legs = build_legs(trade)
        assert len(legs) == 2
        assert legs[0]["volume"] == 500    # 1x put
        assert legs[1]["volume"] == 1000   # 2x call


class TestBuildIronCondor:
    """Test iron condor leg builder."""

    def test_four_legs_correct_types(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[95.50, 95.75, 96.25, 96.50],
            strategy="ic",
            volume=100,
            direction_side="B",
        )
        legs = build_iron_condor(trade)
        assert len(legs) == 4
        assert legs[0]["option_type"] == "P"
        assert legs[1]["option_type"] == "P"
        assert legs[2]["option_type"] == "C"
        assert legs[3]["option_type"] == "C"


class TestBuildCVDOverlay:
    """Test CVD futures overlay builder."""

    def test_cvd_produces_futures_leg(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[96.00],
            option_types=["C"],
            strategy="single",
            volume=500,
            direction_side="B",
            is_cvd=True,
            cvd_price=95.50,
            delta_percent=40.0,
        )
        legs = build_cvd_overlay(trade)
        assert len(legs) == 1
        assert legs[0]["option_type"] is None  # Futures leg
        assert legs[0]["strike"] is None
        assert legs[0]["volume"] == 200  # 500 * 40 / 100


class TestBuildLegsDispatcher:
    """Test the build_legs dispatcher integrates CVD."""

    def test_single_with_cvd(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[96.00],
            option_types=["C"],
            strategy="single",
            volume=500,
            premium=0.04,
            direction_side="B",
            is_cvd=True,
            cvd_price=95.50,
            delta_percent=40.0,
        )
        legs = build_legs(trade)
        assert len(legs) == 2  # Option + futures overlay

    def test_unknown_strategy_raises(self):
        trade = TradeInput(
            contract_codes=["SFRH6"],
            strikes=[96.00],
            strategy="not_a_strategy",
            volume=100,
            direction_side="B",
        )
        with pytest.raises(ValueError, match="Unrecognized"):
            build_legs(trade)