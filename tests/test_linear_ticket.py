# =============================================================================
# Linear ticket generator tests
# =============================================================================
# Tests for the _ln_interleave_by_cp helper and multi-page CP grouping in
# app/services/ticket_generator.py
#
# No database needed — tests the pure helper function directly.
# =============================================================================

import types

import pytest
from app.services.ticket_generator import _ln_interleave_by_cp, _ln_group_by_strike_broker


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


# =============================================================================
# Helpers for _ln_group_by_strike_broker tests
# =============================================================================

def _leg(side, strike, option_type):
    """Minimal OrderLeg-like object for testing _ln_group_by_strike_broker."""
    return types.SimpleNamespace(
        side=side,           # "B" or "S"
        strike=strike,       # float, or None for futures
        option_type=option_type,  # "C", "P", or None for futures
        leg_index=0,
    )


def _opt_row(cp, house, strike, opt_type, side, qty=100):
    """Build a minimal option row dict (matches _build_broker_sections output)."""
    return {
        "cp": cp, "house": house, "strike": strike, "opt_type": opt_type,
        "side": side, "is_fut": False, "qty": qty,
        "mo": "SFRZ6", "price": "0.05", "bracket": "E6", "new_cp": False,
    }


def _fut_row(cp, house, side, qty=100):
    """Build a minimal futures row dict."""
    return {
        "cp": cp, "house": house, "strike": "FUT", "opt_type": "",
        "side": side, "is_fut": True, "qty": qty,
        "mo": "SFRZ6", "price": "0.05", "bracket": "E6", "new_cp": False,
    }


def _make_fly_sections():
    """Build all_sections for a 96.25/96.50/96.75 C fly with 2 brokers.

    Trade: BUY 96.25C, SELL 96.50C, BUY 96.75C
    Broker GKX:  CPs 590 (300 lots) and 690 (200 lots)
    Broker SPNC: CPs 100 (250 lots) and 200 (250 lots)

    _build_broker_sections puts all strikes into buy_rows/sell_rows per broker.
    """
    gkx_buy = [
        _opt_row("GKX", "590", "96.250", "C", "BUY", 300),
        _opt_row("GKX", "590", "96.750", "C", "BUY", 300),
        _opt_row("GKX", "690", "96.250", "C", "BUY", 200),
        _opt_row("GKX", "690", "96.750", "C", "BUY", 200),
    ]
    gkx_sell = [
        _opt_row("GKX", "590", "96.500", "C", "SELL", 300),
        _opt_row("GKX", "690", "96.500", "C", "SELL", 200),
    ]
    spnc_buy = [
        _opt_row("SPNC", "100", "96.250", "C", "BUY", 250),
        _opt_row("SPNC", "100", "96.750", "C", "BUY", 250),
        _opt_row("SPNC", "200", "96.250", "C", "BUY", 250),
        _opt_row("SPNC", "200", "96.750", "C", "BUY", 250),
    ]
    spnc_sell = [
        _opt_row("SPNC", "100", "96.500", "C", "SELL", 250),
        _opt_row("SPNC", "200", "96.500", "C", "SELL", 250),
    ]
    return [
        {
            "broker": "GKX", "fill_qty": 500,
            "buy_rows": gkx_buy, "sell_rows": gkx_sell,
            "buy_opt_total": 1000, "buy_fut_total": 0,
            "sell_opt_total": 500, "sell_fut_total": 0,
            "n_rows": 1 + max(len(gkx_buy), len(gkx_sell)) + 1,
        },
        {
            "broker": "SPNC", "fill_qty": 1000,
            "buy_rows": spnc_buy, "sell_rows": spnc_sell,
            "buy_opt_total": 2000, "buy_fut_total": 0,
            "sell_opt_total": 1000, "sell_fut_total": 0,
            "n_rows": 1 + max(len(spnc_buy), len(spnc_sell)) + 1,
        },
    ]


def _fly_legs():
    """sorted_legs for a 96.25/96.50/96.75 C fly (buy 96.25, sell 96.50, buy 96.75)."""
    return [
        _leg("B", 96.25, "C"),
        _leg("S", 96.50, "C"),
        _leg("B", 96.75, "C"),
    ]


class TestLnGroupByStrikeBroker:

    def test_empty_input_returns_empty(self):
        """Empty all_sections → empty result."""
        result = _ln_group_by_strike_broker([], _fly_legs())
        assert result == []

    def test_fly_section_count(self):
        """3-strike fly × 2 brokers → exactly 6 sections."""
        result = _ln_group_by_strike_broker(_make_fly_sections(), _fly_legs())
        assert len(result) == 6

    def test_fly_section_order(self):
        """Sections come in strike-first, broker-within-strike order."""
        result = _ln_group_by_strike_broker(_make_fly_sections(), _fly_legs())
        expected = [
            ("96.250 C", "BUY",  "GKX"),
            ("96.250 C", "BUY",  "SPNC"),
            ("96.500 C", "SELL", "GKX"),
            ("96.500 C", "SELL", "SPNC"),
            ("96.750 C", "BUY",  "GKX"),
            ("96.750 C", "BUY",  "SPNC"),
        ]
        for i, (label, side, broker) in enumerate(expected):
            sec = result[i]
            assert sec["strike_label"] == label, f"section {i}: strike_label wrong"
            assert sec["side"] == side,          f"section {i}: side wrong"
            assert sec["broker"] == broker,      f"section {i}: broker wrong"

    def test_fly_first_broker_flags(self):
        """is_first_broker_for_strike is True for the first broker of each strike."""
        result = _ln_group_by_strike_broker(_make_fly_sections(), _fly_legs())
        # GKX is always first (index 0, 2, 4); SPNC is always second (1, 3, 5)
        for i in (0, 2, 4):
            assert result[i]["is_first_broker_for_strike"] is True,  f"section {i} should be first"
        for i in (1, 3, 5):
            assert result[i]["is_first_broker_for_strike"] is False, f"section {i} should NOT be first"

    def test_fly_buy_rows_per_section(self):
        """Each buy-side section has only rows for its own strike."""
        result = _ln_group_by_strike_broker(_make_fly_sections(), _fly_legs())
        # Section 0: GKX at 96.250 BUY
        sec0 = result[0]
        assert all(r["strike"] == "96.250" for r in sec0["buy_rows"])
        assert sec0["sell_rows"] == []
        assert len(sec0["buy_rows"]) == 2   # GKX/590 and GKX/690

    def test_fly_sell_rows_per_section(self):
        """Each sell-side section has only rows for its own strike."""
        result = _ln_group_by_strike_broker(_make_fly_sections(), _fly_legs())
        # Section 2: GKX at 96.500 SELL
        sec2 = result[2]
        assert all(r["strike"] == "96.500" for r in sec2["sell_rows"])
        assert sec2["buy_rows"] == []
        assert len(sec2["sell_rows"]) == 2  # GKX/590 and GKX/690

    def test_grand_total_row_sums_preserved(self):
        """All buy/sell rows (and their qtys) are preserved — none lost or duplicated."""
        original = _make_fly_sections()
        result   = _ln_group_by_strike_broker(original, _fly_legs())
        # Sum qty across every row in the original sections
        orig_buy_qty  = sum(r["qty"] for s in original for r in s["buy_rows"]  if not r["is_fut"])
        orig_sell_qty = sum(r["qty"] for s in original for r in s["sell_rows"] if not r["is_fut"])
        # Same check on the regrouped sections
        new_buy_qty   = sum(r["qty"] for s in result  for r in s["buy_rows"]  if not r["is_fut"])
        new_sell_qty  = sum(r["qty"] for s in result  for r in s["sell_rows"] if not r["is_fut"])
        assert new_buy_qty  == orig_buy_qty,  f"buy qty mismatch: {new_buy_qty} != {orig_buy_qty}"
        assert new_sell_qty == orig_sell_qty, f"sell qty mismatch: {new_sell_qty} != {orig_sell_qty}"

    def test_single_strike_single_broker(self):
        """Single call leg, single broker → exactly one section."""
        rows = [_opt_row("GKX", "590", "96.000", "C", "BUY", 500)]
        sections = [{
            "broker": "GKX", "fill_qty": 500,
            "buy_rows": rows, "sell_rows": [],
            "buy_opt_total": 500, "buy_fut_total": 0,
            "sell_opt_total": 0,  "sell_fut_total": 0,
            "n_rows": 3,
        }]
        legs = [_leg("B", 96.00, "C")]
        result = _ln_group_by_strike_broker(sections, legs)
        assert len(result) == 1
        assert result[0]["broker"] == "GKX"
        assert result[0]["is_first_broker_for_strike"] is True

    def test_futures_appended_last(self):
        """CVD trade: option sections first, futures section at the end."""
        opt_buy = [_opt_row("GKX", "590", "96.000", "C", "BUY", 500)]
        fut_sell = [_fut_row("GKX", "590", "SELL", 1000)]
        sections = [{
            "broker": "GKX", "fill_qty": 500,
            "buy_rows": opt_buy, "sell_rows": fut_sell,
            "buy_opt_total": 500, "buy_fut_total": 0,
            "sell_opt_total": 0,  "sell_fut_total": 1000,
            "n_rows": 4,
        }]
        legs = [
            _leg("B", 96.00, "C"),
            _leg("S", None, None),   # futures leg
        ]
        result = _ln_group_by_strike_broker(sections, legs)
        # First section: the option leg; last section: futures
        assert len(result) == 2
        assert result[0]["strike_label"] == "96.000 C"
        assert result[0]["sell_rows"] == []
        assert result[1]["strike_label"] == "FUT"
        assert result[1]["buy_rows"] == []
        assert len(result[1]["sell_rows"]) == 1
