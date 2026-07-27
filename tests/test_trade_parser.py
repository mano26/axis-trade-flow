# =============================================================================
# Trade Parser Tests
# =============================================================================
# Regression tests for the SOFR trade string parser. Each test case
# represents a known trade string format from the production VBA tool.
#
# Run with: pytest tests/test_trade_parser.py -v
# =============================================================================

import pytest
from app.services.trade_parser import parse_trade_input, ParseError
from app.services.strategy_handlers import build_legs


class TestBasicParsing:
    """Test fundamental parsing behavior."""

    def test_empty_string_raises(self):
        with pytest.raises(ParseError, match="Empty"):
            parse_trade_input("")

    def test_no_volume_raises(self):
        with pytest.raises(ParseError, match="No volume"):
            parse_trade_input("SFRH6 C 96.00")

    def test_no_contract_code_raises(self):
        with pytest.raises(ParseError, match="No contract code"):
            parse_trade_input("C 96.00 4/500")

    def test_no_strikes_raises(self):
        with pytest.raises(ParseError, match="No strikes"):
            parse_trade_input("SFRH6 C 4/500")


class TestDirectionParsing:
    """Test buy/sell direction detection from price format."""

    def test_slash_format_is_buy(self):
        """price/qty format → BUY (debit)."""
        result = parse_trade_input("SFRH6 C 96.00 4/500")
        assert result[0].direction_side == "B"
        assert result[0].volume == 500
        assert result[0].premium == pytest.approx(0.04)

    def test_at_format_is_sell(self):
        """qty@price format → SELL (credit)."""
        result = parse_trade_input("SFRH6 C 96.00 500@4")
        assert result[0].direction_side == "S"
        assert result[0].volume == 500
        assert result[0].premium == pytest.approx(0.04)


class TestSingleOptionParsing:
    """Test single option leg parsing."""

    def test_single_call(self):
        result = parse_trade_input("SFRH6 C 96.00 4/500")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "single"
        assert t.option_types == ["C"]
        assert t.strikes == [96.00]
        assert t.volume == 500

    def test_single_put(self):
        result = parse_trade_input("SFRH6 P 95.75 3/200")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "single"
        assert t.option_types == ["P"]
        assert t.strikes == [95.75]


class TestSpreadParsing:
    """Test two-leg strategy parsing."""

    def test_call_spread(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 CS 4/500")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "cs"
        assert len(t.strikes) == 2
        assert t.is_call_centric is True

    def test_put_spread(self):
        result = parse_trade_input("SFRH6 P 95.75 96.00 PS 3/300")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "ps"
        assert t.is_put_centric is True

    def test_risk_reversal(self):
        result = parse_trade_input("SFRH6 95.50 96.00 RR 4/500")
        assert len(result) == 1
        assert result[0].strategy == "rr"
        assert len(result[0].strikes) == 2

    def test_rr_cvd_futures_direction_buy_put_qualifier(self):
        """
        BUY RR CVD with (PUT) qualifier: buy put + sell call → net negative
        delta → CVD futures should be BUY.  Regression for bug where SELL
        futures was produced instead.
        """
        from app.services.strategy_handlers import build_legs
        legs = build_legs(
            parse_trade_input("SFRZ6 95.375 96.375 RR CVD 95.87 D 32 0.75/1000 (PUT)")[0]
        )
        fut = [l for l in legs if l.get("option_type") is None and l.get("strike") is None]
        assert len(fut) == 1, "Expected exactly one futures leg"
        assert fut[0]["side"] == "B", f"Expected BUY futures, got {fut[0]['side']}"

    def test_rr_cvd_futures_direction_no_qualifier(self):
        """RR CVD without any qualifier should also produce BUY futures."""
        from app.services.strategy_handlers import build_legs
        legs = build_legs(
            parse_trade_input("SFRZ6 95.375 96.375 RR CVD 95.87 D 32 0.75/1000")[0]
        )
        fut = [l for l in legs if l.get("option_type") is None and l.get("strike") is None]
        assert len(fut) == 1
        assert fut[0]["side"] == "B"

    def test_rr_cvd_leg_structure(self):
        """RR CVD should produce: BUY low-strike PUT, SELL high-strike CALL, BUY futures."""
        from app.services.strategy_handlers import build_legs
        legs = build_legs(
            parse_trade_input("SFRZ6 95.375 96.375 RR CVD 95.87 D 32 0.75/1000")[0]
        )
        opt = [l for l in legs if l.get("option_type") is not None]
        fut = [l for l in legs if l.get("option_type") is None and l.get("strike") is None]
        assert opt[0]["side"] == "B" and opt[0]["option_type"] == "P"
        assert opt[1]["side"] == "S" and opt[1]["option_type"] == "C"
        assert fut[0]["side"] == "B"

class TestStraddleStrangleParsing:
    """Test straddle and strangle parsing."""

    def test_straddle(self):
        result = parse_trade_input("SFRH6 95.75 ^ 3/100")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "straddle"
        assert t.is_straddle is True
        assert t.option_types == ["C", "P"]

    def test_strangle(self):
        result = parse_trade_input("SFRH6 95.50 95.75 ^^ 2/300")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "strangle"
        assert t.is_strangle is True

    def test_straddle_option_types_calls_first(self):
        result = parse_trade_input("SFRH6 95.75 ^ 3/100")
        t = result[0]
        assert t.option_types == ["C", "P"]

    def test_calendar_straddle_spread_two_segments(self):
        """0QU6 96.0625 3QU6 96.125 ^ SPRD 2000@9.5 -> sell 0QU6 ^, buy 3QU6 ^."""
        result = parse_trade_input("0QU6 96.0625 3QU6 96.125 ^ SPRD 2000@9.5")
        assert len(result) == 2
        seg0, seg1 = result
        # Both straddles
        assert seg0.strategy == "straddle"
        assert seg1.strategy == "straddle"
        # Opposite directions (sell first, buy second for qty@price credit)
        assert seg0.direction_side == "S"
        assert seg1.direction_side == "B"
        # Each contract gets its own strike
        assert seg0.strikes == [96.0625]
        assert seg1.strikes == [96.125]
        # Contract codes correct
        assert "0QU6" in seg0.contract_codes
        assert "3QU6" in seg1.contract_codes

    def test_calendar_straddle_shared_strike(self):
        """Single strike shared across both contracts."""
        result = parse_trade_input("SFRM6 SFRU6 96.50 ^ 5/200")
        assert len(result) == 2
        assert result[0].strikes == [96.50]
        assert result[1].strikes == [96.50]
        assert result[0].direction_side == "B"
        assert result[1].direction_side == "S"

    def test_calendar_strangle_spread(self):
        """Two-contract strangle calendar spread."""
        result = parse_trade_input("SFRM6 95.75 96.00 SFRU6 95.50 96.25 ^^ 3/500")
        assert len(result) == 2
        assert result[0].strategy == "strangle"
        assert result[1].strategy == "strangle"
        assert result[0].direction_side == "B"
        assert result[1].direction_side == "S"


class TestButterflyParsing:
    """Test butterfly and condor parsing."""

    def test_call_butterfly(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 96.50 C FLY 2/200")
        assert len(result) == 1
        assert result[0].strategy == "bflyc"
        assert len(result[0].strikes) == 3

    def test_put_butterfly(self):
        result = parse_trade_input("SFRH6 P 95.50 95.75 96.00 P FLY 2/200")
        assert len(result) == 1
        assert result[0].strategy == "bflyp"

    def test_call_condor(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 96.50 96.75 C CON 1/100")
        assert len(result) == 1
        assert result[0].strategy == "condorc"
        assert len(result[0].strikes) == 4


class TestChristmasTreeParsing:
    """Test christmas tree strategy parsing."""

    def test_call_tree(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 96.50 TREE 2/200")
        assert len(result) == 1
        assert result[0].strategy == "ctree"

    def test_put_tree(self):
        result = parse_trade_input("SFRH6 P 95.50 95.75 96.00 PTREE 2/200")
        assert len(result) == 1
        assert result[0].strategy == "ptree"


class TestVSTradeParsing:
    """Test VS (versus) two-leg trade parsing."""

    def test_vs_trade_produces_two_legs(self):
        result = parse_trade_input("SFRH6 C 96.00 VS SFRM6 C 96.25 4/500")
        assert len(result) == 2

    def test_vs_trade_opposite_directions(self):
        result = parse_trade_input("SFRH6 C 96.00 VS SFRM6 C 96.25 4/500")
        assert result[0].direction_side == "B"
        assert result[1].direction_side == "S"

    def test_vs_suppresses_premium(self):
        result = parse_trade_input("SFRH6 C 96.00 VS SFRM6 C 96.25 4/500")
        assert result[0].suppress_premium is True
        assert result[1].suppress_premium is True

    def test_vs_at_start_raises(self):
        with pytest.raises(ParseError, match="no left segment"):
            parse_trade_input("VS SFRM6 C 96.25 4/500")


class TestCVDParsing:
    """Test CVD (covered / delta hedge) parsing."""

    def test_cvd_basic(self):
        result = parse_trade_input("SFRH6 C 96.00 3/500 CVD 95.50 D 40")
        assert len(result) == 1
        t = result[0]
        assert t.is_cvd is True
        assert t.cvd_price == 95.50
        assert t.delta_percent == 40.0

    def test_cvd_with_override(self):
        result = parse_trade_input("SFRH6 C 96.00 3/500 CVD 95.50(+) D 40")
        t = result[0]
        assert t.cvd_has_override is True
        assert t.cvd_override_side == "+"

    def test_strip_cvd_single_futures_leg(self):
        """STRIP + CVD: the futures hedge fires once for the whole strip, not per leg.

        2QU6 95.625 95.50 95.375 P STRIP CVD 95.985 D 30 1000@7.5 should produce
        3 put legs (1000 each) and exactly ONE futures leg of 300 lots, not 300 per put.
        """
        result = parse_trade_input(
            "2QU6 95.625 95.50 95.375 P STRIP CVD 95.985 D 30 1000 @ 7.5"
        )
        assert len(result) == 3

        # Only the last segment carries CVD
        assert result[0].is_cvd is False
        assert result[1].is_cvd is False
        assert result[2].is_cvd is True
        assert result[2].delta_percent == 30.0

        # Build all legs and count futures
        total_futures_vol = 0
        put_count = 0
        for seg in result:
            legs = build_legs(seg)
            for leg in legs:
                if leg.get("option_type") is None and leg.get("strike") is None:
                    total_futures_vol += leg["volume"]
                elif leg.get("option_type") == "P":
                    put_count += 1

        assert put_count == 3
        assert total_futures_vol == 300   # 30% of 1000, once only


class TestBracketWrapperParsing:
    """Test [] bracket wrapper syntax."""

    def test_bracket_buy(self):
        result = parse_trade_input("[SFRH6 C 96.00, SFRM6 P 95.50] 4/500")
        assert len(result) == 2
        for t in result:
            assert t.direction_side == "B"
            assert t.suppress_premium is True
            assert t.volume == 500

    def test_bracket_sell(self):
        result = parse_trade_input("[SFRH6 C 96.00] 500@4")
        assert len(result) == 1
        assert result[0].direction_side == "S"

    def test_bracket_missing_close_raises(self):
        with pytest.raises(ParseError, match="missing"):
            parse_trade_input("[SFRH6 C 96.00 4/500")


class TestContractCodes:
    """Test contract code recognition."""

    def test_sfr_prefix(self):
        result = parse_trade_input("SFRH6 C 96.00 4/500")
        assert "SFRH6" in result[0].contract_codes

    def test_short_dated_adds_pack_helper(self):
        result = parse_trade_input("0QZ5 C 96.00 4/500")
        codes = result[0].contract_codes
        assert "0QZ5" in codes
        assert "S0" in codes

    def test_sr3_prefix(self):
        result = parse_trade_input("SR3H6 C 96.00 4/500")
        assert "SR3H6" in result[0].contract_codes


class TestRatioSpreads:
    """Test ratio spread parsing."""

    def test_1x2_call_spread(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 1X2 CS 4/500")
        t = result[0]
        assert t.ratios == [1, 2]
        assert t.strategy == "cs"

    def test_1x3x2_butterfly(self):
        result = parse_trade_input("SFRM6 96.25 96.50 96.625 C FLY 1X3X2 2/500")
        t = result[0]
        assert t.ratios == [1, 3, 2]
        assert t.strategy == "bflyc"

    def test_4_part_ratio_condor(self):
        result = parse_trade_input("SFRM6 96.25 96.50 96.75 97.00 C CON 1X2X2X2 7/1000")
        t = result[0]
        assert t.ratios == [1, 2, 2, 2]
        assert t.strategy == "condorc"

    def test_1x2_straddle(self):
        result = parse_trade_input("SFRH6 96.25 ^ 1X2 4/500")
        t = result[0]
        assert t.ratios == [1, 2]
        assert t.strategy == "straddle"


class TestSegmentVolumeMultiplier:
    """Test '(NX)' segment-level volume multiplier in VS/WITH trades."""

    def test_ps_3x_vs_single_call(self):
        """PS (3X) VS single call: PS legs get 3x base volume, call stays at base."""
        result = parse_trade_input(
            "SFRZ6 96.1875 95.8125 PS (3X) VS SFRZ6 96.4375 C "
            "CVD 96.105 D 126 1833 @ 35.25"
        )
        assert len(result) == 2
        ps_seg, call_seg = result
        assert ps_seg.strategy == "ps"
        assert ps_seg.volume_multiplier == 3
        assert ps_seg.volume == 1833 * 3
        assert call_seg.volume_multiplier == 1
        assert call_seg.volume == 1833

    def test_multiplier_legs_scaled(self):
        """Built legs reflect the multiplied volume."""
        result = parse_trade_input(
            "SFRZ6 96.1875 95.8125 PS (3X) VS SFRZ6 96.4375 C 1833@35.25"
        )
        ps_seg = result[0]
        legs = build_legs(ps_seg)
        assert all(leg["volume"] == 5499 for leg in legs)

    def test_no_multiplier_defaults_to_one(self):
        result = parse_trade_input("SFRH6 96.00 96.25 CS 4/500")
        assert result[0].volume_multiplier == 1
        assert result[0].volume == 500

    def test_multiplier_no_separator(self):
        """(NX) also works on a plain single-segment trade (no VS/WITH)."""
        result = parse_trade_input("SFRH6 96.00 96.25 CS (2X) 4/500")
        t = result[0]
        assert t.volume_multiplier == 2
        assert t.volume == 1000


class TestTrailingParenthetical:
    """Test stripping of trailing parenthetical notes."""

    def test_strip_trailing_note(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 CS 4/500 (96.50)")
        assert len(result) == 1
        assert result[0].strategy == "cs"

    def test_strip_text_note(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 96.50 C TREE 2/1000 (2 legs)")
        assert len(result) == 1
        assert result[0].strategy == "ctree"