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
from app.services.ticket_generator import (
    _ln_interleave_by_cp,
    _ln_group_by_strike_broker,
    _ln_paginate_sections,
    _LN_ROWS_PAGE_1,
    _LN_ROWS_CONT,
    _LN_SECTION_OH,
)


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


# =============================================================================
# Helpers for _ln_paginate_sections tests
# =============================================================================

def _make_ln_section(broker, strike_label, side, n_rows,
                     is_first_broker=True, is_fut=False):
    """Build a minimal post-_ln_group_by_strike_broker section dict for paginator tests."""
    if is_fut:
        rows = [_fut_row(broker, f"{200 + i}", "SELL") for i in range(n_rows)]
        return {
            "broker": broker, "strike_label": "FUT", "side": "",
            "fill_qty": 500, "n_rows": n_rows + 2,
            "buy_rows": [],    "sell_rows": rows,
            "buy_opt_total": 0, "buy_fut_total": 0,
            "sell_opt_total": 0, "sell_fut_total": n_rows * 100,
            "is_continuation": False,
            "is_first_broker_for_strike": True,  # always True per _ln_group_by_strike_broker
        }
    rows = [_opt_row(broker, f"{200 + i}", strike_label, "P", side)
            for i in range(n_rows)]
    return {
        "broker": broker, "strike_label": strike_label, "side": side,
        "fill_qty": 500, "n_rows": n_rows + 2,
        "buy_rows":  rows if side == "BUY"  else [],
        "sell_rows": rows if side == "SELL" else [],
        "buy_opt_total":  n_rows * 100 if side == "BUY"  else 0,
        "buy_fut_total":  0,
        "sell_opt_total": n_rows * 100 if side == "SELL" else 0,
        "sell_fut_total": 0,
        "is_continuation": False,
        "is_first_broker_for_strike": is_first_broker,
    }


def _total_data_rows(pages):
    """Count all data rows across all pages (used to detect clipping/duplication)."""
    return sum(
        len(sec.get("buy_rows", [])) + len(sec.get("sell_rows", []))
        for page in pages
        for sec in page
    )


def _section_row_units(sec):
    """Row-unit cost as the FIXED paginator computes it.

    Components:
      data_rows + _LN_SECTION_OH (label + thead + subtotal)
      +1 when is_first_broker_for_strike and not is_continuation and strike_label present
         (the .ln-strike-header visual row)
    """
    data = len(sec.get("buy_rows", [])) + len(sec.get("sell_rows", []))
    units = data + _LN_SECTION_OH
    if (sec.get("is_first_broker_for_strike")
            and not sec.get("is_continuation")
            and sec.get("strike_label")):
        units += 1
    return units


# =============================================================================
# _ln_paginate_sections tests
# =============================================================================

class TestLnPaginateSections:
    """Tests for _ln_paginate_sections.

    The key scenario — 2 brokers × 5 CPs × 2 strikes + futures — matches the
    test spec from the bug report.  Expected values are derived from the FIXED
    constants: rows_page_1=12, rows_cont=24, OH=3, +1 for strike headers.

    Bugs caught by this suite:
      1. eff_budget logic bug — uses rows_cont on page 1 (causes page 1 overflow)
      2. _LN_SECTION_OH=2 undercounts overhead (misses subtotal row)
      3. Strike-header row not accounted for in budget
      4. Rows silently clipped in print (caught by test_all_rows_preserved)
    """

    def test_empty_returns_one_empty_page(self):
        """Empty input → exactly one empty page (HTML renderer needs page 0 to exist)."""
        pages = _ln_paginate_sections([], _LN_ROWS_PAGE_1, _LN_ROWS_CONT)
        assert pages == [[]]

    def test_single_small_section_fits_page_1(self):
        """One 3-row section with a strike header (cost=3+3+1=7) fits on page 1 alone."""
        sec = _make_ln_section("GKX", "95.5000 P", "BUY", 3, is_first_broker=True)
        pages = _ln_paginate_sections([sec], _LN_ROWS_PAGE_1, _LN_ROWS_CONT)
        assert len(pages) == 1
        assert pages[0][0]["broker"] == "GKX"

    def test_two_brokers_five_cps_two_strikes_plus_futures(self):
        """Comprehensive 2-broker/5-CP/2-strike/futures scenario → exactly 3 pages.

        Input sections (after _ln_group_by_strike_broker):
          GKX  95.5000 P BUY  5 rows is_first=True   → 5+3+1=9  row-units
          SPNC 95.5000 P BUY  5 rows is_first=False  → 5+3  =8  row-units
          GKX  95.2500 P SELL 5 rows is_first=True   → 5+3+1=9  row-units
          SPNC 95.2500 P SELL 5 rows is_first=False  → 5+3  =8  row-units
          GKX  FUT       SELL 2 rows is_first=True   → 2+3+1=6  row-units
          SPNC FUT       SELL 2 rows is_first=True   → 2+3+1=6  row-units

        Expected pages (rows_page_1=12, rows_cont=24):
          Page 1 → [GKX 95.5000]                         (9 units ≤ 12)
          Page 2 → [SPNC 95.5000, GKX 95.2500]           (8+9=17 ≤ 24)
          Page 3 → [SPNC 95.2500, GKX FUT, SPNC FUT]     (8+6+6=20 ≤ 24)

        Fails when:
          • eff_budget bug present → page 1 gets 3 sections (21 units), only 2 pages total
          • OH=2 → page 2 gets 3 sections; page 3 only has futures
          • Strike-header +1 missing → page 2 gets 3 sections; page 3 only has futures
        """
        sections = [
            _make_ln_section("GKX",  "95.5000 P", "BUY",  5, is_first_broker=True),
            _make_ln_section("SPNC", "95.5000 P", "BUY",  5, is_first_broker=False),
            _make_ln_section("GKX",  "95.2500 P", "SELL", 5, is_first_broker=True),
            _make_ln_section("SPNC", "95.2500 P", "SELL", 5, is_first_broker=False),
            _make_ln_section("GKX",  "FUT", "SELL", 2, is_first_broker=True,  is_fut=True),
            _make_ln_section("SPNC", "FUT", "SELL", 2, is_first_broker=True,  is_fut=True),
        ]
        pages = _ln_paginate_sections(sections, 12, 24)

        # ── page count ────────────────────────────────────────────────────────
        assert len(pages) == 3, (
            f"Expected 3 pages, got {len(pages)}. "
            f"Sections per page: {[len(p) for p in pages]}"
        )

        # ── page 1: only GKX 95.5000 ─────────────────────────────────────────
        assert len(pages[0]) == 1, \
            f"Page 1: expected 1 section, got {len(pages[0])}"
        assert pages[0][0]["broker"] == "GKX"
        assert pages[0][0]["strike_label"] == "95.5000 P"

        # ── page 2: SPNC 95.5000 then GKX 95.2500 ───────────────────────────
        assert len(pages[1]) == 2, \
            f"Page 2: expected 2 sections, got {len(pages[1])}"
        assert pages[1][0]["broker"] == "SPNC" and pages[1][0]["strike_label"] == "95.5000 P"
        assert pages[1][1]["broker"] == "GKX"  and pages[1][1]["strike_label"] == "95.2500 P"

        # ── page 3: SPNC 95.2500, GKX FUT, SPNC FUT ─────────────────────────
        assert len(pages[2]) == 3, \
            f"Page 3: expected 3 sections, got {len(pages[2])}"
        assert pages[2][0]["broker"] == "SPNC" and pages[2][0]["strike_label"] == "95.2500 P"
        assert pages[2][1]["broker"] == "GKX"  and pages[2][1]["strike_label"] == "FUT"
        assert pages[2][2]["broker"] == "SPNC" and pages[2][2]["strike_label"] == "FUT"

    def test_all_rows_preserved_no_clipping(self):
        """Every input row appears in exactly one page — none silently lost.

        This is the direct regression test for the max-height:200mm print-CSS
        clipping bug: when the paginator overflows page 1 the excess rows bleed
        to a wrong physical page or are hidden by overflow:hidden.  Correct
        pagination means the HTML page div never overflows at all.
        """
        sections = [
            _make_ln_section("GKX",  "95.5000 P", "BUY",  5, is_first_broker=True),
            _make_ln_section("SPNC", "95.5000 P", "BUY",  5, is_first_broker=False),
            _make_ln_section("GKX",  "95.2500 P", "SELL", 5, is_first_broker=True),
            _make_ln_section("SPNC", "95.2500 P", "SELL", 5, is_first_broker=False),
            _make_ln_section("GKX",  "FUT", "SELL", 2, is_first_broker=True,  is_fut=True),
            _make_ln_section("SPNC", "FUT", "SELL", 2, is_first_broker=True,  is_fut=True),
        ]
        input_rows = sum(
            len(s.get("buy_rows", [])) + len(s.get("sell_rows", []))
            for s in sections
        )
        pages = _ln_paginate_sections(sections, 12, 24)
        output_rows = _total_data_rows(pages)
        assert output_rows == input_rows, (
            f"Row count mismatch: {output_rows} output vs {input_rows} input — "
            "rows were lost (printed blank) or duplicated"
        )

    def test_page1_budget_respected(self):
        """Page 1 row units must not exceed rows_page_1=12.

        The eff_budget bug makes page 1 use rows_cont=26 as budget as soon as
        any section is placed, so two sections that exceed rows_page_1=12 but
        fit in rows_cont=26 both land on page 1 — causing physical overflow.
        """
        # GKX: 4 data rows + OH=3 + strike-header=1 → 8 units
        # SPNC: 4 data rows + OH=3 (no strike header) → 7 units
        # Together: 15 > 12 → should be on separate pages.
        # With eff_budget bug: 15 ≤ 26 → both on page 1.
        sections = [
            _make_ln_section("GKX",  "95.5000 P", "BUY", 4, is_first_broker=True),
            _make_ln_section("SPNC", "95.5000 P", "BUY", 4, is_first_broker=False),
        ]
        pages = _ln_paginate_sections(sections, 12, 24)
        p1_units = sum(_section_row_units(sec) for sec in pages[0])
        assert p1_units <= 12, (
            f"Page 1 has {p1_units} row-units but budget is 12 — "
            "eff_budget bug: page 1 is using rows_cont as its budget"
        )

    def test_continuation_pages_within_budget(self):
        """Each continuation page (2+) must not exceed rows_cont budget."""
        sections = [
            _make_ln_section("GKX",  "95.5000 P", "BUY",  5, is_first_broker=True),
            _make_ln_section("SPNC", "95.5000 P", "BUY",  5, is_first_broker=False),
            _make_ln_section("GKX",  "95.2500 P", "SELL", 5, is_first_broker=True),
            _make_ln_section("SPNC", "95.2500 P", "SELL", 5, is_first_broker=False),
            _make_ln_section("GKX",  "FUT", "SELL", 2, is_first_broker=True,  is_fut=True),
            _make_ln_section("SPNC", "FUT", "SELL", 2, is_first_broker=True,  is_fut=True),
        ]
        pages = _ln_paginate_sections(sections, 12, 24)
        for page_num, page in enumerate(pages[1:], start=2):
            units = sum(_section_row_units(sec) for sec in page)
            assert units <= 24, (
                f"Page {page_num} has {units} row-units, exceeds rows_cont=24"
            )

    def test_large_section_splits_across_pages(self):
        """A section with more data rows than a full page budget is split into chunks."""
        sec = _make_ln_section("GKX", "95.5000 P", "BUY", 30, is_first_broker=True)
        pages = _ln_paginate_sections([sec], _LN_ROWS_PAGE_1, _LN_ROWS_CONT)
        total_chunks = sum(len(p) for p in pages)
        assert total_chunks >= 2, (
            f"30-row section should be split into multiple chunks, got {total_chunks} chunk(s)"
        )
        # All 30 rows must be preserved
        assert _total_data_rows(pages) == 30

    def test_oversized_first_section_always_placed(self):
        """A section too large for page budget is placed if it's the first on that page.

        Without this the paginator would loop endlessly trying to find a page
        with enough room.  The render CSS is the last line of defence for overflow.
        """
        # 15-row section costs 15+3+1=19 > page1 budget of 12 — still gets placed
        sec = _make_ln_section("GKX", "95.5000 P", "BUY", 15, is_first_broker=True)
        pages = _ln_paginate_sections([sec], 12, 24)
        assert _total_data_rows(pages) == 15, "Oversized section rows must not be dropped"

    def test_split_chunks_have_is_continuation_flag(self):
        """After a section is split: first chunk is_continuation=False, rest True."""
        sec = _make_ln_section("GKX", "95.5000 P", "BUY", 30, is_first_broker=True)
        pages = _ln_paginate_sections([sec], _LN_ROWS_PAGE_1, _LN_ROWS_CONT)
        all_chunks = [chunk for page in pages for chunk in page]
        assert not all_chunks[0].get("is_continuation"), \
            "First chunk must not be marked as continuation"
        for chunk in all_chunks[1:]:
            assert chunk.get("is_continuation"), \
                f"Subsequent chunk must be marked is_continuation=True: {chunk}"
