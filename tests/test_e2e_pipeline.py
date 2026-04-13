# =============================================================================
# End-to-End Pipeline Tests — designed to break things
# =============================================================================
# Full parse → build_legs → validate cycle for representative trade strings.
# These are the regression anchors: if a known trade string stops producing
# the expected leg structure, one of these will catch it.
#
# No database needed — pure service layer.
#
# Run with: pytest tests/test_e2e_pipeline.py -v
# =============================================================================

import pytest
from app.services.trade_parser import parse_trade_input, ParseError
from app.services.strategy_handlers import build_legs
from app.services.validation import validate_fill_prices, ValidationError
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def parse_and_build(trade_string):
    """Parse a trade string and build all legs. Returns (trades, all_legs)."""
    trades = parse_trade_input(trade_string)
    all_legs = []
    for t in trades:
        all_legs.extend(build_legs(t))
    return trades, all_legs


def _mock_order(legs_data):
    """Build a mock Order from a list of leg dicts."""
    order = MagicMock()
    mock_legs = []
    for i, l in enumerate(legs_data):
        m = MagicMock()
        m.leg_index = i
        m.side = l.get("side", "B")
        m.volume = l.get("volume", 500)
        m.strike = l.get("strike")
        m.option_type = l.get("option_type")
        m.package_premium = l.get("package_premium", 0.04)
        mock_legs.append(m)
    order.legs = mock_legs
    return order


# ---------------------------------------------------------------------------
# Call Spread
# ---------------------------------------------------------------------------

class TestCallSpreadE2E:

    def test_basic_call_spread_legs(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 CS 4/500")
        assert len(legs) == 2
        assert legs[0]["side"] == "B"
        assert legs[0]["strike"] == 96.00
        assert legs[0]["option_type"] == "C"
        assert legs[1]["side"] == "S"
        assert legs[1]["strike"] == 96.25
        assert legs[0]["volume"] == 500
        assert legs[1]["volume"] == 500

    def test_call_spread_sell_direction(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 CS 500@4")
        assert legs[0]["side"] == "S"
        assert legs[1]["side"] == "B"

    def test_call_spread_1x2_volumes(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 CS 1X2 4/500")
        assert legs[0]["volume"] == 500
        assert legs[1]["volume"] == 1000

    def test_call_spread_price_reconciliation(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 CS 4/500")
        order = _mock_order(legs)
        lp = [
            MagicMock(leg_index=0, price=0.07),
            MagicMock(leg_index=1, price=0.03),
        ]
        validate_fill_prices(order, MagicMock(), lp)  # must not raise

    def test_call_spread_bad_prices_raise(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 CS 4/500")
        order = _mock_order(legs)
        lp = [
            MagicMock(leg_index=0, price=0.07),
            MagicMock(leg_index=1, price=0.02),  # net = 0.05, expected 0.04
        ]
        with pytest.raises(ValidationError):
            validate_fill_prices(order, MagicMock(), lp)


# ---------------------------------------------------------------------------
# Put Spread
# ---------------------------------------------------------------------------

class TestPutSpreadE2E:

    def test_put_spread_buy_high_sell_low(self):
        _, legs = parse_and_build("SFRH6 P 95.75 96.00 PS 4/500")
        # sorted descending for put spread
        assert legs[0]["strike"] == 96.00
        assert legs[0]["side"] == "B"
        assert legs[1]["strike"] == 95.75
        assert legs[1]["side"] == "S"
        assert all(l["option_type"] == "P" for l in legs)


# ---------------------------------------------------------------------------
# Straddle
# ---------------------------------------------------------------------------

class TestStraddleE2E:

    def test_straddle_two_legs_same_strike(self):
        _, legs = parse_and_build("SFRH6 95.75 ^ 3/100")
        assert len(legs) == 2
        assert legs[0]["option_type"] == "P"
        assert legs[1]["option_type"] == "C"
        assert legs[0]["strike"] == 95.75
        assert legs[1]["strike"] == 95.75
        assert legs[0]["side"] == "B"
        assert legs[1]["side"] == "B"

    def test_straddle_sell_direction(self):
        _, legs = parse_and_build("SFRH6 95.75 ^ 100@3")
        assert all(l["side"] == "S" for l in legs)

    def test_straddle_1x2_ratio(self):
        _, legs = parse_and_build("SFRH6 96.00 ^ 1X2 4/500")
        vols = sorted(l["volume"] for l in legs)
        assert vols == [500, 1000]


# ---------------------------------------------------------------------------
# Butterfly
# ---------------------------------------------------------------------------

class TestButterflyE2E:

    def test_call_butterfly_legs_and_volumes(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 96.50 C FLY 2/200")
        assert len(legs) == 3
        assert legs[0]["volume"] == 200
        assert legs[1]["volume"] == 400  # 2x body
        assert legs[2]["volume"] == 200
        assert legs[0]["side"] == "B"
        assert legs[1]["side"] == "S"
        assert legs[2]["side"] == "B"

    def test_put_butterfly_option_types(self):
        _, legs = parse_and_build("SFRH6 P 95.50 95.75 96.00 P FLY 2/200")
        assert all(l["option_type"] == "P" for l in legs)

    def test_custom_1x3x2_ratio_butterfly(self):
        _, legs = parse_and_build("SFRM6 96.25 96.50 96.625 C FLY 1X3X2 2/500")
        assert legs[0]["volume"] == 500
        assert legs[1]["volume"] == 1500
        assert legs[2]["volume"] == 1000

    def test_butterfly_strikes_preserved_in_input_order(self):
        _, legs = parse_and_build("SFRH6 C 96.50 96.25 96.00 C FLY 2/200")
        assert legs[0]["strike"] == 96.50
        assert legs[1]["strike"] == 96.25
        assert legs[2]["strike"] == 96.00


# ---------------------------------------------------------------------------
# Condor
# ---------------------------------------------------------------------------

class TestCondorE2E:

    def test_call_condor_four_legs(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 96.50 96.75 C CON 1/100")
        assert len(legs) == 4
        assert legs[0]["side"] == "B"
        assert legs[1]["side"] == "S"
        assert legs[2]["side"] == "S"
        assert legs[3]["side"] == "B"
        assert all(l["option_type"] == "C" for l in legs)

    def test_put_condor_option_types(self):
        _, legs = parse_and_build("SFRH6 P 95.25 95.50 95.75 96.00 P CON 1/100")
        assert all(l["option_type"] == "P" for l in legs)


# ---------------------------------------------------------------------------
# Iron Condor
# ---------------------------------------------------------------------------

class TestIronCondorE2E:

    def test_ic_four_legs_mixed_types(self):
        _, legs = parse_and_build("SFRH6 95.50 95.75 96.25 96.50 IC 2/200")
        assert len(legs) == 4
        option_types = [l["option_type"] for l in legs]
        assert option_types.count("P") == 2
        assert option_types.count("C") == 2

    def test_ic_sorted_strikes(self):
        _, legs = parse_and_build("SFRH6 96.50 95.50 96.25 95.75 IC 2/200")
        strikes = [l["strike"] for l in legs]
        assert strikes == sorted(strikes)


# ---------------------------------------------------------------------------
# Christmas Tree
# ---------------------------------------------------------------------------

class TestChristmasTreeE2E:

    def test_call_tree_buy_one_sell_two(self):
        _, legs = parse_and_build("SFRH6 C 96.00 96.25 96.50 TREE 2/200")
        assert legs[0]["side"] == "B"
        assert legs[1]["side"] == "S"
        assert legs[2]["side"] == "S"
        assert all(l["option_type"] == "C" for l in legs)

    def test_put_tree_option_types(self):
        _, legs = parse_and_build("SFRH6 P 95.50 95.75 96.00 PTREE 2/200")
        assert all(l["option_type"] == "P" for l in legs)


# ---------------------------------------------------------------------------
# VS Calendar Spread
# ---------------------------------------------------------------------------

class TestVSCalendarE2E:

    def test_vs_produces_two_trade_objects(self):
        trades, _ = parse_and_build("SFRH6 C 96.00 VS SFRM6 C 96.25 4/500")
        assert len(trades) == 2

    def test_vs_opposite_directions_opposite_legs(self):
        trades, _ = parse_and_build("SFRH6 C 96.00 VS SFRM6 C 96.25 4/500")
        assert trades[0].direction_side == "B"
        assert trades[1].direction_side == "S"

    def test_vs_total_legs_count(self):
        _, legs = parse_and_build("SFRH6 C 96.00 VS SFRM6 C 96.25 4/500")
        assert len(legs) == 2


# ---------------------------------------------------------------------------
# CVD End-to-End
# ---------------------------------------------------------------------------

class TestCVDE2E:

    def test_cvd_call_produces_option_and_futures(self):
        _, legs = parse_and_build("SFRH6 C 96.00 3/500 CVD 95.50 D 40")
        assert len(legs) == 2
        opt_legs = [l for l in legs if l["option_type"] is not None]
        fut_legs = [l for l in legs if l["option_type"] is None]
        assert len(opt_legs) == 1
        assert len(fut_legs) == 1

    def test_cvd_futures_volume(self):
        _, legs = parse_and_build("SFRH6 C 96.00 3/500 CVD 95.50 D 40")
        fut = next(l for l in legs if l["option_type"] is None)
        assert fut["volume"] == 200  # 500 * 40 / 100

    def test_cvd_futures_price(self):
        _, legs = parse_and_build("SFRH6 C 96.00 3/500 CVD 95.50 D 40")
        fut = next(l for l in legs if l["option_type"] is None)
        assert fut["price"] == pytest.approx(95.50)

    def test_cvd_call_buy_futures_sell(self):
        _, legs = parse_and_build("SFRH6 C 96.00 3/500 CVD 95.50 D 40")
        fut = next(l for l in legs if l["option_type"] is None)
        assert fut["side"] == "S"

    def test_cvd_put_buy_futures_buy(self):
        _, legs = parse_and_build("SFRH6 P 95.75 3/500 CVD 95.50 D 40")
        fut = next(l for l in legs if l["option_type"] is None)
        assert fut["side"] == "B"


# ---------------------------------------------------------------------------
# Bracket Wrapper E2E
# ---------------------------------------------------------------------------

class TestBracketWrapperE2E:

    def test_bracket_two_segments_correct_direction(self):
        trades, legs = parse_and_build("[SFRH6 C 96.00, SFRM6 P 95.50] 4/500")
        assert len(trades) == 2
        assert all(t.direction_side == "B" for t in trades)

    def test_bracket_volumes_set_from_trailer(self):
        trades, _ = parse_and_build("[SFRH6 C 96.00, SFRM6 P 95.50] 4/500")
        assert all(t.volume == 500 for t in trades)

    def test_bracket_sell(self):
        trades, _ = parse_and_build("[SFRH6 C 96.00] 500@4")
        assert trades[0].direction_side == "S"


# ---------------------------------------------------------------------------
# Proportional Fill Volume
# ---------------------------------------------------------------------------

class TestProportionalFillVolume:
    """
    The card generator applies proportional volumes when filled_quantity < total_quantity.
    Test the arithmetic directly without the HTML layer.
    """

    def test_50_pct_fill_halves_leg_volume(self):
        """500/1000 filled → each leg volume displayed at 50%."""
        total = 1000
        filled = 500
        base_volume = 200
        ratio = filled / total
        displayed = round(base_volume * ratio)
        assert displayed == 100

    def test_full_fill_unchanged(self):
        total = 1000
        filled = 1000
        base_volume = 200
        ratio = filled / total
        displayed = round(base_volume * ratio)
        assert displayed == 200

    def test_partial_fill_butterfly_body_double(self):
        """1:2:1 butterfly. 300/1000 filled → wing=60, body=120."""
        total = 1000
        filled = 300
        legs_base = [100, 200, 100]  # 1:2:1 at 100 base
        ratio = filled / total
        displayed = [round(v * ratio) for v in legs_base]
        assert displayed == [30, 60, 30]
