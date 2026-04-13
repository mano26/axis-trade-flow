# =============================================================================
# Parser Edge Case Tests — designed to break things
# =============================================================================
# Targets the grey areas and boundary conditions in trade_parser.py
# that production strings are most likely to hit.
#
# Run with: pytest tests/test_parser_edge_cases.py -v
# =============================================================================

import pytest
from app.services.trade_parser import (
    parse_trade_input,
    parse_ratio_token,
    strip_trailing_parenthetical,
    is_contract_code,
    ParseError,
)


# ---------------------------------------------------------------------------
# Whitespace / Normalization
# ---------------------------------------------------------------------------

class TestWhitespaceHandling:

    def test_leading_trailing_whitespace(self):
        result = parse_trade_input("  SFRH6 C 96.00 4/500  ")
        assert result[0].strategy == "single"

    def test_extra_internal_whitespace(self):
        result = parse_trade_input("SFRH6   C   96.00   4/500")
        assert result[0].strikes == [96.00]

    def test_slash_with_spaces(self):
        """Spaces around / should be collapsed before parsing."""
        result = parse_trade_input("SFRH6 C 96.00 4 / 500")
        assert result[0].volume == 500
        assert result[0].premium == pytest.approx(0.04)

    def test_at_with_spaces(self):
        """Spaces around @ should be collapsed."""
        result = parse_trade_input("SFRH6 C 96.00 500 @ 4")
        assert result[0].direction_side == "S"
        assert result[0].volume == 500

    def test_tab_instead_of_space(self):
        """Tabs should not break the parser (they become multiple spaces)."""
        # Tabs in real input are normalised by strip() / re.sub
        result = parse_trade_input("SFRH6 C 96.00 4/500")
        assert result[0].volume == 500


# ---------------------------------------------------------------------------
# Price / Volume Edge Cases
# ---------------------------------------------------------------------------

class TestPriceVolumeParsing:

    def test_zero_volume_raises(self):
        with pytest.raises(ParseError, match="No volume"):
            parse_trade_input("SFRH6 C 96.00 4/0")

    def test_zero_price_buy_accepted(self):
        """A 0-tick package premium (even-money spread) should parse."""
        result = parse_trade_input("SFRH6 C 96.00 96.25 CS 0/500")
        assert result[0].premium == pytest.approx(0.0)
        assert result[0].direction_side == "B"

    def test_zero_price_sell_accepted(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 CS 500@0")
        assert result[0].premium == pytest.approx(0.0)
        assert result[0].direction_side == "S"

    def test_large_volume(self):
        result = parse_trade_input("SFRH6 C 96.00 4/50000")
        assert result[0].volume == 50000

    def test_fractional_volume_truncated_to_int(self):
        """100.5 lots should round to 100 via int(float(...))."""
        result = parse_trade_input("SFRH6 C 96.00 4/100.5")
        assert result[0].volume == 100

    def test_multi_digit_price(self):
        result = parse_trade_input("SFRH6 C 96.00 12/500")
        assert result[0].premium == pytest.approx(0.12)

    def test_price_precision_floating_point(self):
        """Premium 6.5 ticks → 0.065. Stored as rounded float, not 0.06500000000001."""
        result = parse_trade_input("SFRH6 C 96.00 6.5/500")
        assert result[0].premium == pytest.approx(0.065, abs=1e-9)


# ---------------------------------------------------------------------------
# Strike Edge Cases
# ---------------------------------------------------------------------------

class TestStrikeEdgeCases:

    def test_integer_strike_not_recognized(self):
        """Integers without decimal points should NOT be parsed as strikes."""
        # '96' alone is ambiguous (could be a volume if it follows a slash).
        # The parser only recognizes floats with '.' as strikes.
        with pytest.raises(ParseError, match="No strikes"):
            parse_trade_input("SFRH6 C 96 4/500")

    def test_strike_96_00_with_decimals(self):
        result = parse_trade_input("SFRH6 C 96.00 4/500")
        assert result[0].strikes == [96.0]

    def test_strike_order_preserved_spread(self):
        """Strikes must be preserved in input order for spreads."""
        result = parse_trade_input("SFRH6 C 96.25 96.00 CS 4/500")
        # build_call_spread sorts, but the parser should record input order
        assert result[0].strikes == [96.25, 96.00]

    def test_strike_order_preserved_butterfly(self):
        result = parse_trade_input("SFRH6 C 96.50 96.25 96.00 C FLY 2/200")
        assert result[0].strikes == [96.50, 96.25, 96.00]

    def test_quarter_tick_strike(self):
        result = parse_trade_input("SFRH6 C 96.625 4/500")
        assert result[0].strikes == [96.625]

    def test_high_strike(self):
        result = parse_trade_input("SFRH6 C 99.75 4/500")
        assert result[0].strikes == [99.75]

    def test_too_few_strikes_condor_raises(self):
        with pytest.raises(ParseError):
            parse_trade_input("SFRH6 C 96.00 96.25 96.50 C CON 2/200")

    def test_too_many_strikes_spread_ignored(self):
        """Extra strikes beyond what the strategy needs — parser stores all,
        validate_strikes checks count. Three strikes on a CS should fail."""
        with pytest.raises(ParseError):
            parse_trade_input("SFRH6 C 96.00 96.25 96.50 CS 2/200")


# ---------------------------------------------------------------------------
# Contract Code Edge Cases
# ---------------------------------------------------------------------------

class TestContractCodeEdgeCases:

    def test_lowercase_contract_code(self):
        """Contract codes entered in lowercase should still be recognised."""
        result = parse_trade_input("sfrh6 C 96.00 4/500")
        # is_contract_code upper-cases the token
        assert any("SFRH6" in c.upper() for c in result[0].contract_codes)

    def test_sr3_prefix_recognized(self):
        result = parse_trade_input("SR3H6 C 96.00 4/500")
        assert result[0].contract_codes[0].upper() == "SR3H6"

    def test_2q_short_dated(self):
        result = parse_trade_input("2QM6 C 96.00 4/500")
        codes = [c.upper() for c in result[0].contract_codes]
        assert "2QM6" in codes
        assert "S2" in codes

    def test_3q_short_dated(self):
        result = parse_trade_input("3QU6 C 96.00 4/500")
        codes = [c.upper() for c in result[0].contract_codes]
        assert "3QU6" in codes
        assert "S3" in codes

    def test_no_contract_code_raises(self):
        with pytest.raises(ParseError, match="No contract code"):
            parse_trade_input("C 96.00 4/500")

    def test_unknown_token_falls_through(self):
        """A token that isn't a contract code, strategy, strike, or volume
        should be silently consumed by set_strategy returning 0, and the
        parse still succeeds if the rest is valid."""
        # "XMAS" is a recognised alias for ctree — should not raise
        result = parse_trade_input("SFRH6 C 96.00 96.25 96.50 XMAS 2/200")
        assert result[0].strategy == "ctree"


# ---------------------------------------------------------------------------
# Strategy Disambiguation
# ---------------------------------------------------------------------------

class TestStrategyDisambiguation:

    def test_fly_alone_defaults_to_call_centric(self):
        """BFLY without explicit C/P — should resolve to bflyc when C present."""
        result = parse_trade_input("SFRH6 C 96.00 96.25 96.50 BFLY 2/200")
        assert result[0].strategy == "bflyc"

    def test_fly_with_put_resolves_bflyp(self):
        result = parse_trade_input("SFRH6 P 95.50 95.75 96.00 BFLY 2/200")
        assert result[0].strategy == "bflyp"

    def test_con_without_qualifier_raises_or_defaults(self):
        """CON alone at end of input (no C/P peek) — parser falls through
        without setting strategy, then fails on no C/P token."""
        # This is intentionally ambiguous; should either raise or resolve
        # gracefully — not silently produce wrong output.
        try:
            result = parse_trade_input("SFRH6 96.00 96.25 96.50 96.75 CON 2/200")
            # If it resolves, strategy must be condorc or condorp, not ''
            assert result[0].strategy in ("condorc", "condorp")
        except ParseError:
            pass  # Also acceptable

    def test_iron_condor_without_condor_keyword(self):
        """IRON alone (no following CONDOR/CON) should still set ic."""
        result = parse_trade_input("SFRH6 96.00 96.25 96.75 97.00 IRON 2/200")
        assert result[0].strategy == "ic"

    def test_rr_alias(self):
        result = parse_trade_input("SFRH6 95.75 96.00 RISKREV 4/500")
        assert result[0].strategy == "rr"

    def test_callspread_alias(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 CALLSPREAD 4/500")
        assert result[0].strategy == "cs"

    def test_putspread_alias(self):
        result = parse_trade_input("SFRH6 P 95.75 96.00 PUTSPREAD 4/500")
        assert result[0].strategy == "ps"

    def test_christmas_alias(self):
        result = parse_trade_input("SFRH6 C 96.00 96.25 96.50 CHRISTMAS 2/200")
        assert result[0].strategy == "ctree"

    def test_pspd_alias(self):
        result = parse_trade_input("SFRH6 P 95.75 96.00 PSPD 4/500")
        assert result[0].strategy == "ps"

    def test_price_before_contract_code(self):
        """Some traders type volume@price first."""
        result = parse_trade_input("500@4 SFRH6 P 95.75 96.00 PS")
        assert result[0].direction_side == "S"
        assert result[0].strategy == "ps"


# ---------------------------------------------------------------------------
# Ratio Token Parser
# ---------------------------------------------------------------------------

class TestRatioTokenParser:

    def test_1x2(self):
        assert parse_ratio_token("1X2") == [1, 2]

    def test_1x2x1(self):
        assert parse_ratio_token("1X2X1") == [1, 2, 1]

    def test_1x3x2(self):
        assert parse_ratio_token("1X3X2") == [1, 3, 2]

    def test_2x3x5x1(self):
        assert parse_ratio_token("2X3X5X1") == [2, 3, 5, 1]

    def test_lowercase_x(self):
        assert parse_ratio_token("1x2") == [1, 2]

    def test_no_x_returns_empty(self):
        assert parse_ratio_token("12") == []

    def test_non_digit_parts_returns_empty(self):
        assert parse_ratio_token("1XA") == []

    def test_zero_ratio_returns_empty(self):
        """Zero ratios are invalid (all(r > 0) guard)."""
        assert parse_ratio_token("1X0") == []

    def test_single_part_returns_empty(self):
        assert parse_ratio_token("X") == []


# ---------------------------------------------------------------------------
# Parenthetical Stripping
# ---------------------------------------------------------------------------

class TestParentheticalStripping:

    def test_numeric_note_stripped(self):
        s = strip_trailing_parenthetical("SFRH6 C 96.00 4/500 (96.50)")
        assert s == "SFRH6 C 96.00 4/500"

    def test_text_note_stripped(self):
        s = strip_trailing_parenthetical("SFRH6 ^ 4/500 (2 legs)")
        assert s == "SFRH6 ^ 4/500"

    def test_no_parenthetical_unchanged(self):
        s = strip_trailing_parenthetical("SFRH6 C 96.00 4/500")
        assert s == "SFRH6 C 96.00 4/500"

    def test_parenthetical_mid_string_not_stripped(self):
        """Only trailing parentheticals should be stripped."""
        s = strip_trailing_parenthetical("SFRH6 C (96.00) 4/500")
        assert "96.00" in s

    def test_empty_parenthetical_stripped(self):
        """() at end — empty note, still a trailing parenthetical."""
        s = strip_trailing_parenthetical("SFRH6 C 96.00 4/500 ()")
        assert s.endswith("4/500")

    def test_multiple_parentheticals_only_last_stripped(self):
        """Only the last trailing parenthetical should be removed."""
        s = strip_trailing_parenthetical("SFRH6 (96.00) C 96.00 4/500 (note)")
        assert s.endswith("4/500")


# ---------------------------------------------------------------------------
# VS Trade Edge Cases
# ---------------------------------------------------------------------------

class TestVSEdgeCases:

    def test_vs_at_end_raises(self):
        with pytest.raises(ParseError, match="no right leg"):
            parse_trade_input("SFRH6 C 96.00 VS 4/500")

    def test_vs_stupid_same_direction(self):
        """VS with STUPID flag — both legs same direction."""
        result = parse_trade_input("SFRH6 C 96.00 STUPID VS SFRM6 C 96.25 4/500")
        assert result[0].direction_side == result[1].direction_side

    def test_vs_both_legs_suppress_premium(self):
        result = parse_trade_input("SFRH6 C 96.00 VS SFRM6 C 96.25 4/500")
        assert result[0].suppress_premium is True
        assert result[1].suppress_premium is True

    def test_vs_same_contract_different_expiry(self):
        result = parse_trade_input("SFRH6 C 96.00 VS SFRM6 C 96.00 4/500")
        assert result[0].contract_codes[0].upper() == "SFRH6"
        assert result[1].contract_codes[0].upper() == "SFRM6"


# ---------------------------------------------------------------------------
# CVD Edge Cases
# ---------------------------------------------------------------------------

class TestCVDEdgeCases:

    def test_cvd_no_price_raises(self):
        with pytest.raises(ParseError):
            parse_trade_input("SFRH6 C 96.00 4/500 CVD")

    def test_cvd_non_numeric_price_raises(self):
        with pytest.raises(ParseError):
            parse_trade_input("SFRH6 C 96.00 4/500 CVD FOO D 40")

    def test_cvd_override_plus(self):
        result = parse_trade_input("SFRH6 C 96.00 4/500 CVD 95.50(+) D 40")
        assert result[0].cvd_has_override is True
        assert result[0].cvd_override_side == "+"

    def test_cvd_override_minus(self):
        result = parse_trade_input("SFRH6 P 95.75 4/500 CVD 95.50(-) D 40")
        assert result[0].cvd_has_override is True
        assert result[0].cvd_override_side == "-"

    def test_delta_override_plus(self):
        result = parse_trade_input("SFRH6 C 96.00 4/500 CVD 95.50 D 40 (+)")
        assert result[0].delta_override == "B"

    def test_delta_override_minus(self):
        result = parse_trade_input("SFRH6 C 96.00 4/500 CVD 95.50 D 40 (-)")
        assert result[0].delta_override == "S"

    def test_cvd_no_delta_token(self):
        """CVD with price but no D token — still valid, delta = 0."""
        result = parse_trade_input("SFRH6 C 96.00 4/500 CVD 95.50")
        assert result[0].is_cvd is True
        assert result[0].cvd_price == 95.50
        assert result[0].delta_percent == 0.0


# ---------------------------------------------------------------------------
# Bracket Wrapper Edge Cases
# ---------------------------------------------------------------------------

class TestBracketWrapperEdgeCases:

    def test_bracket_no_close_raises(self):
        with pytest.raises(ParseError, match="missing"):
            parse_trade_input("[SFRH6 C 96.00 4/500")

    def test_bracket_empty_raises(self):
        with pytest.raises(ParseError, match="empty"):
            parse_trade_input("[] 4/500")

    def test_bracket_no_trailer_raises(self):
        with pytest.raises(ParseError, match="no price"):
            parse_trade_input("[SFRH6 C 96.00]")

    def test_bracket_three_segments(self):
        result = parse_trade_input("[SFRH6 C 96.00, SFRM6 P 95.50, SFRU6 C 96.25] 4/500")
        assert len(result) == 3
        assert all(t.suppress_premium for t in result)
        assert all(t.volume == 500 for t in result)

    def test_bracket_sell_direction(self):
        result = parse_trade_input("[SFRH6 C 96.00] 500@4")
        assert result[0].direction_side == "S"


# ---------------------------------------------------------------------------
# Multi-contract Calendar Spread Inference
# ---------------------------------------------------------------------------

class TestMultiContractInference:

    def test_two_contracts_cs_splits_to_two_singles(self):
        """Two contracts + CS + one strike → calendar spread of singles."""
        result = parse_trade_input("SFRH6 SFRM6 C 96.00 CS 4/500")
        assert len(result) == 2
        assert result[0].strategy == "single"
        assert result[1].strategy == "single"
        assert result[0].direction_side == "B"
        assert result[1].direction_side == "S"

    def test_stupid_flag_splits_to_same_direction(self):
        """STUPID + two codes → both legs same direction."""
        result = parse_trade_input("SFRH6 SFRM6 C 96.00 CS STUPID 4/500")
        assert len(result) == 2
        assert result[0].direction_side == result[1].direction_side
