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


class TestStraddleStrangleParsing:
    """Test straddle and strangle parsing."""

    def test_straddle(self):
        result = parse_trade_input("SFRH6 95.75 ^ 3/100")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "straddle"
        assert t.is_straddle is True
        assert t.option_types == ["P", "C"]

    def test_strangle(self):
        result = parse_trade_input("SFRH6 95.50 95.75 ^^ 2/300")
        assert len(result) == 1
        t = result[0]
        assert t.strategy == "strangle"
        assert t.is_strangle is True


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
        with pytest.raises(ParseError, match="no left leg"):
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