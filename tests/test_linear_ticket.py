# =============================================================================
# Linear ticket generator tests
# =============================================================================
# Tests for the _ln_interleave_by_cp helper and multi-page CP grouping in
# app/services/ticket_generator.py
#
# No database needed — tests the pure helper function directly.
# =============================================================================

import pytest
from app.services.ticket_generator import _ln_interleave_by_cp


def _row(cp, house, side, strike):
    """Build a minimal row dict for testing."""
    return {
        "cp": cp, "house": house, "strike": strike,
        "side": side, "is_fut": False, "qty": 100,
        "opt_type": "P", "mo": "SFRZ6", "price": "0.05",
        "bracket": "E6", "new_cp": False,
    }


class TestLnInterleaveByCp:

    def test_single_cp_two_legs(self):
        """One CP with one buy leg and one sell leg → buy before sell."""
        buy = [_row("SPNC", "590", "BUY", "95.625")]
        sell = [_row("SPNC", "590", "SELL", "95.750")]
        result = _ln_interleave_by_cp(buy, sell)
        assert len(result) == 2
        assert result[0]["side"] == "BUY"
        assert result[1]["side"] == "SELL"

    def test_two_cps_each_two_legs(self):
        """Two CPs, each with a buy+sell leg → CP1 all, then CP2 all."""
        buy = [
            _row("SPNC", "590", "BUY", "95.625"),
            _row("SPNC", "690", "BUY", "95.625"),
        ]
        sell = [
            _row("SPNC", "590", "SELL", "95.750"),
            _row("SPNC", "690", "SELL", "95.750"),
        ]
        result = _ln_interleave_by_cp(buy, sell)
        assert len(result) == 4
        # CP 590 rows first (buy then sell), then CP 690 rows
        assert result[0]["house"] == "590" and result[0]["side"] == "BUY"
        assert result[1]["house"] == "590" and result[1]["side"] == "SELL"
        assert result[2]["house"] == "690" and result[2]["side"] == "BUY"
        assert result[3]["house"] == "690" and result[3]["side"] == "SELL"

    def test_spread_four_legs_per_cp(self):
        """Spread: two buy legs and two sell legs per CP, multiple CPs."""
        buy = [
            _row("SPNC", "590", "BUY", "95.625"),
            _row("SPNC", "590", "BUY", "95.875"),
            _row("SPNC", "690", "BUY", "95.625"),
            _row("SPNC", "690", "BUY", "95.875"),
        ]
        sell = [
            _row("SPNC", "590", "SELL", "95.750"),
            _row("SPNC", "590", "SELL", "95.8125"),
            _row("SPNC", "690", "SELL", "95.750"),
            _row("SPNC", "690", "SELL", "95.8125"),
        ]
        result = _ln_interleave_by_cp(buy, sell)
        assert len(result) == 8
        # First 4 rows: SPNC/590 (2 buys then 2 sells)
        assert all(r["house"] == "590" for r in result[:4])
        assert result[0]["side"] == "BUY"   and result[0]["strike"] == "95.625"
        assert result[1]["side"] == "BUY"   and result[1]["strike"] == "95.875"
        assert result[2]["side"] == "SELL"  and result[2]["strike"] == "95.750"
        assert result[3]["side"] == "SELL"  and result[3]["strike"] == "95.8125"
        # Next 4 rows: SPNC/690
        assert all(r["house"] == "690" for r in result[4:])

    def test_ten_cps_four_legs_each(self):
        """Simulate the real trade: 10 CPs × 4 legs = 40 rows.

        After interleaving, every group of 4 consecutive rows must belong
        to the same (cp, house) key.
        """
        cp_houses = [
            ("SPNC", "590"), ("SPNC", "690"), ("TDUN", "273"), ("JML", "125"),
            ("PVR", "521"), ("RXA", "690"), ("EDE", "365"), ("TMX", "125"),
            ("CRC", "690"), ("DWT", "845"),
        ]
        buy_strikes = ["95.625", "95.875"]
        sell_strikes = ["95.750", "95.8125"]

        buy_rows = []
        sell_rows = []
        for cp, house in cp_houses:
            for s in buy_strikes:
                buy_rows.append(_row(cp, house, "BUY", s))
            for s in sell_strikes:
                sell_rows.append(_row(cp, house, "SELL", s))

        result = _ln_interleave_by_cp(buy_rows, sell_rows)
        assert len(result) == 40

        # Every block of 4 consecutive rows must be the same CP/house
        for i, (cp, house) in enumerate(cp_houses):
            block = result[i * 4:(i + 1) * 4]
            assert all(r["cp"] == cp and r["house"] == house for r in block), (
                f"Block {i} ({cp}/{house}) has mixed CPs: "
                f"{[(r['cp'], r['house']) for r in block]}"
            )
            # Buy legs before sell legs within each CP block
            assert block[0]["side"] == "BUY"
            assert block[1]["side"] == "BUY"
            assert block[2]["side"] == "SELL"
            assert block[3]["side"] == "SELL"

    def test_buy_only_cp(self):
        """CP that has only buy rows (no sell) is included correctly."""
        buy = [_row("MANO", "100", "BUY", "95.625")]
        sell = []
        result = _ln_interleave_by_cp(buy, sell)
        assert len(result) == 1
        assert result[0]["side"] == "BUY"

    def test_sell_only_cp(self):
        """CP that appears only in sell rows is appended after buy-side CPs."""
        buy = [_row("SPNC", "590", "BUY", "95.625")]
        sell = [_row("MANO", "100", "SELL", "95.750")]
        result = _ln_interleave_by_cp(buy, sell)
        assert len(result) == 2
        assert result[0]["house"] == "590"
        assert result[1]["house"] == "100"

    def test_preserves_identity_not_equality(self):
        """Two rows with identical field values stay distinct in the result."""
        r1 = _row("SPNC", "590", "BUY", "95.625")
        r2 = _row("SPNC", "590", "SELL", "95.625")  # same fields except side
        result = _ln_interleave_by_cp([r1], [r2])
        assert result[0] is r1
        assert result[1] is r2
