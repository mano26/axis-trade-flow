# =============================================================================
# Keypunch CSV Generator Tests
# =============================================================================
# Tests for app/services/keypunch_generator.py
#
# No database needed — uses MagicMock objects to simulate model instances.
# =============================================================================

import csv
import io
import pytest
from datetime import date
from unittest.mock import MagicMock

from app.services.keypunch_generator import (
    generate_keypunch_csv,
    _expiry_to_yyyymm,
    _traded_as,
)


# ---------------------------------------------------------------------------
# Unit tests: _expiry_to_yyyymm
# ---------------------------------------------------------------------------

class TestExpiryToYyyyMm:
    def test_mar26(self):
        assert _expiry_to_yyyymm("MAR26") == "202603"

    def test_dec27(self):
        assert _expiry_to_yyyymm("DEC27") == "202712"

    def test_jan25(self):
        assert _expiry_to_yyyymm("JAN25") == "202501"

    def test_sep30(self):
        assert _expiry_to_yyyymm("SEP30") == "203009"

    def test_lowercase_passthrough(self):
        # Upper-cased inside the function; lowercase input should still work
        assert _expiry_to_yyyymm("mar26") == "202603"

    def test_unknown_month_passthrough(self):
        assert _expiry_to_yyyymm("XYZ26") == "XYZ26"

    def test_empty_string(self):
        assert _expiry_to_yyyymm("") == ""

    def test_none(self):
        assert _expiry_to_yyyymm(None) == ""


# ---------------------------------------------------------------------------
# Unit tests: _traded_as
# ---------------------------------------------------------------------------

def _make_leg(option_type=None, strike=None, volume=1):
    leg = MagicMock()
    leg.option_type = option_type
    leg.strike = strike
    leg.volume = volume
    return leg


class TestTradedAs:
    def test_single_option_is_O(self):
        order = MagicMock()
        order.legs = [_make_leg(option_type="C", strike=96.0)]
        assert _traded_as(order) == "O"

    def test_two_option_legs_is_S(self):
        order = MagicMock()
        order.legs = [
            _make_leg(option_type="C", strike=96.0),
            _make_leg(option_type="P", strike=95.5),
        ]
        assert _traded_as(order) == "S"

    def test_single_futures_leg_is_S(self):
        order = MagicMock()
        order.legs = [_make_leg(option_type=None, strike=None)]
        assert _traded_as(order) == "S"

    def test_option_plus_futures_is_S(self):
        order = MagicMock()
        order.legs = [
            _make_leg(option_type="C", strike=96.0),
            _make_leg(option_type=None, strike=None),
        ]
        assert _traded_as(order) == "S"


# ---------------------------------------------------------------------------
# Integration tests: generate_keypunch_csv
# ---------------------------------------------------------------------------

def _make_order(
    trade_date=date(2026, 3, 15),
    ticket_display="0042",
    house="GFI",
    account="TEST",
    bk_broker=None,
    keypunch_order_override=None,
    total_quantity=500,
    legs=None,
    fills=None,
):
    """Build a mock Order suitable for passing to generate_keypunch_csv."""
    order = MagicMock()
    order.trade_date = trade_date
    order.ticket_display = ticket_display
    order.house = house
    order.account = account
    order.bk_broker = bk_broker
    order.keypunch_order_override = keypunch_order_override
    order.total_quantity = total_quantity
    order.legs = legs or []
    order.fills = fills or []
    return order


def _make_fill(fill_qty=500, stamp_in="093000", stamp_out="093005",
               leg_prices=None, counterparties=None):
    fill = MagicMock()
    fill.fill_quantity = fill_qty
    fill.stamp_time_in = stamp_in
    fill.stamp_time_out = stamp_out
    fill.leg_prices = leg_prices or []
    fill.counterparties = counterparties or []
    return fill


def _make_fill_leg_price(leg_index, price):
    lp = MagicMock()
    lp.leg_index = leg_index
    lp.price = price
    return lp


def _make_cp(qty=500, broker="GKX", symbol="SPNC", cp_house="590",
             bracket="I", futures_quantity=None):
    cp = MagicMock()
    cp.quantity = qty
    cp.broker = broker
    cp.symbol = symbol
    cp.cp_house = cp_house
    cp.bracket = bracket
    cp.notes = None
    cp.futures_quantity = futures_quantity
    return cp


def _parse_csv(csv_text):
    reader = csv.DictReader(io.StringIO(csv_text))
    return list(reader)


class TestGenerateKeypunchCsv:

    def test_header_row_present(self):
        order = _make_order()
        csv_text = generate_keypunch_csv(order)
        assert "Trade Date" in csv_text
        assert "Opp Firm" in csv_text
        assert "Opp Brkr" in csv_text
        assert "Time Brkt" in csv_text

    def test_single_leg_single_cp(self):
        leg = _make_leg(option_type="C", strike=96.0, volume=500)
        leg.leg_index = 0
        leg.side = "B"
        leg.contract_type = "SR3"
        leg.expiry = "MAR26"
        leg.price = 0.0275

        lp = _make_fill_leg_price(0, 0.0275)
        cp = _make_cp(qty=500, broker="GKX", symbol="SPNC", cp_house="590", bracket="I")

        fill = _make_fill(fill_qty=500, stamp_in="093000", stamp_out="093005",
                          leg_prices=[lp], counterparties=[cp])

        order = _make_order(
            total_quantity=500,
            legs=[leg],
            fills=[fill],
        )

        rows = _parse_csv(generate_keypunch_csv(order))
        assert len(rows) == 1
        row = rows[0]

        assert row["Trade Date"] == "20260315"
        assert row["Firm"] == "GFI"
        assert row["Product"] == "SR3"
        assert row["Contract"] == "202603"
        assert row["P/C"] == "C"
        assert row["Strike Price"] == "96.0000"
        assert row["B/S"] == "B"
        assert row["QTY"] == "500"
        assert row["Price"] == "0.0275"
        assert row["Account"] == "TEST"
        assert row["Order"] == "0042"
        assert row["Org"] == "C"
        assert row["CTI"] == "4"
        assert row["Traded As"] == "O"
        assert row["Order Type"] == "L"
        assert row["Brkr"] == "GKX"
        assert row["Opp Firm"] == "590"
        assert row["Opp Brkr"] == "SPNC"
        assert row["Time Brkt"] == "I"
        assert row["In Time"] == "093000"
        assert row["Out Time"] == "093005"
        assert row["Brkr Rect Ind"] == "F"
        assert row["Undly"] == ""
        assert row["BK Brkr"] == ""

    def test_spread_traded_as_S(self):
        """Two-leg call spread should have Traded As = S."""
        legs = []
        for i, strike in enumerate([96.0, 96.25]):
            leg = _make_leg(option_type="C", strike=strike, volume=500)
            leg.leg_index = i
            leg.side = "B" if i == 0 else "S"
            leg.contract_type = "SR3"
            leg.expiry = "MAR26"
            leg.price = None
            legs.append(leg)

        lp0 = _make_fill_leg_price(0, 0.0400)
        lp1 = _make_fill_leg_price(1, 0.0125)
        cp = _make_cp(qty=500)
        fill = _make_fill(leg_prices=[lp0, lp1], counterparties=[cp])

        order = _make_order(total_quantity=500, legs=legs, fills=[fill])
        rows = _parse_csv(generate_keypunch_csv(order))
        assert len(rows) == 2
        for row in rows:
            assert row["Traded As"] == "S"

    def test_keypunch_order_override_used(self):
        leg = _make_leg(option_type="C", strike=96.0, volume=100)
        leg.leg_index = 0
        leg.side = "B"
        leg.contract_type = "SR3"
        leg.expiry = "JUN26"
        leg.price = 0.0100
        cp = _make_cp(qty=100)
        fill = _make_fill(counterparties=[cp])
        order = _make_order(
            total_quantity=100,
            keypunch_order_override="MYORDER99",
            legs=[leg],
            fills=[fill],
        )
        rows = _parse_csv(generate_keypunch_csv(order))
        assert rows[0]["Order"] == "MYORDER99"

    def test_qty_scaled_by_leg_ratio(self):
        """
        A 2-leg butterfly: total_quantity=500, legs at volumes 500, 1000, 500.
        A CP with qty=500 should produce row quantities 500, 1000, 500.
        """
        legs = []
        for i, vol in enumerate([500, 1000, 500]):
            leg = _make_leg(option_type="C", strike=96.0 + i * 0.25, volume=vol)
            leg.leg_index = i
            leg.side = "B" if i != 1 else "S"
            leg.contract_type = "SR3"
            leg.expiry = "MAR26"
            leg.price = 0.01
            legs.append(leg)

        lps = [_make_fill_leg_price(i, 0.01) for i in range(3)]
        cp = _make_cp(qty=500)
        fill = _make_fill(leg_prices=lps, counterparties=[cp])
        order = _make_order(total_quantity=500, legs=legs, fills=[fill])

        rows = _parse_csv(generate_keypunch_csv(order))
        assert len(rows) == 3
        assert int(rows[0]["QTY"]) == 500
        assert int(rows[1]["QTY"]) == 1000
        assert int(rows[2]["QTY"]) == 500

    def test_multiple_fills_multiple_cps(self):
        """Two fills with one CP each → 2 rows total (single-leg order)."""
        leg = _make_leg(option_type="P", strike=95.75, volume=250)
        leg.leg_index = 0
        leg.side = "S"
        leg.contract_type = "SR3"
        leg.expiry = "SEP26"
        leg.price = 0.0050

        cp1 = _make_cp(qty=250, broker="GKX", symbol="MANO")
        cp2 = _make_cp(qty=250, broker="GKX", symbol="CITADEL")
        fill1 = _make_fill(fill_qty=250, stamp_in="093000", stamp_out="093010",
                           leg_prices=[_make_fill_leg_price(0, 0.0050)],
                           counterparties=[cp1])
        fill2 = _make_fill(fill_qty=250, stamp_in="093500", stamp_out="093510",
                           leg_prices=[_make_fill_leg_price(0, 0.0050)],
                           counterparties=[cp2])

        order = _make_order(total_quantity=500, legs=[leg], fills=[fill1, fill2])
        rows = _parse_csv(generate_keypunch_csv(order))
        assert len(rows) == 2
        assert rows[0]["In Time"] == "093000"
        assert rows[0]["Out Time"] == "093010"
        assert rows[1]["In Time"] == "093500"
        assert rows[1]["Out Time"] == "093510"
        assert rows[0]["Opp Brkr"] == "MANO"
        assert rows[1]["Opp Brkr"] == "CITADEL"

    def test_futures_leg_pc_blank(self):
        """Futures legs should have blank P/C and blank Strike Price."""
        leg = _make_leg(option_type=None, strike=None, volume=100)
        leg.leg_index = 0
        leg.side = "B"
        leg.contract_type = "SR3"
        leg.expiry = "MAR26"
        leg.price = 96.0000
        cp = _make_cp(qty=100)
        fill = _make_fill(leg_prices=[_make_fill_leg_price(0, 96.0000)],
                          counterparties=[cp])
        order = _make_order(total_quantity=100, legs=[leg], fills=[fill])
        rows = _parse_csv(generate_keypunch_csv(order))
        assert rows[0]["P/C"] == ""
        assert rows[0]["Strike Price"] == ""
        assert rows[0]["Traded As"] == "S"

    def test_cvd_futures_quantity_used(self):
        """When cp.futures_quantity is set on a futures leg, use it."""
        leg = _make_leg(option_type=None, strike=None, volume=45)
        leg.leg_index = 0
        leg.side = "B"
        leg.contract_type = "SR3"
        leg.expiry = "MAR26"
        leg.price = 96.0
        # futures_quantity override (e.g. trader chose ceil of 11.25)
        cp = _make_cp(qty=500, futures_quantity=12)
        fill = _make_fill(leg_prices=[_make_fill_leg_price(0, 96.0)],
                          counterparties=[cp])
        order = _make_order(total_quantity=500, legs=[leg], fills=[fill])
        rows = _parse_csv(generate_keypunch_csv(order))
        assert rows[0]["QTY"] == "12"

    def test_bk_broker_populated(self):
        leg = _make_leg(option_type="C", strike=96.0, volume=100)
        leg.leg_index = 0
        leg.side = "B"
        leg.contract_type = "SR3"
        leg.expiry = "MAR26"
        leg.price = 0.01
        cp = _make_cp(qty=100)
        fill = _make_fill(leg_prices=[_make_fill_leg_price(0, 0.01)],
                          counterparties=[cp])
        order = _make_order(total_quantity=100, bk_broker="BGC", legs=[leg], fills=[fill])
        rows = _parse_csv(generate_keypunch_csv(order))
        assert rows[0]["BK Brkr"] == "BGC"

    def test_static_columns(self):
        """Verify hardcoded static column values across every row."""
        leg = _make_leg(option_type="C", strike=96.0, volume=100)
        leg.leg_index = 0
        leg.side = "B"
        leg.contract_type = "SR3"
        leg.expiry = "MAR26"
        leg.price = 0.01
        cp = _make_cp(qty=100)
        fill = _make_fill(leg_prices=[_make_fill_leg_price(0, 0.01)],
                          counterparties=[cp])
        order = _make_order(total_quantity=100, legs=[leg], fills=[fill])
        rows = _parse_csv(generate_keypunch_csv(order))
        row = rows[0]
        assert row["Firm Exchange"] == "CME"
        assert row["Product Exchange"] == "CME"
        assert row["Org"] == "C"
        assert row["CTI"] == "4"
        assert row["Order Type"] == "L"
        assert row["Brkr Rect Ind"] == "F"
        assert row["Alloc Type"] == ""
        assert row["Avg Px Grp ID/Carry Firm"] == ""
        assert row["Carry Account"] == ""
        assert row["CTR"] == ""
        assert row["In Time Ind"] == ""
        assert row["Brkr Rect"] == ""
        assert row["Exec Time"] == ""
        assert row["Exec Ind"] == ""
        assert row["Out Time Ind"] == ""
        assert row["Undly"] == ""

    def test_no_fills_returns_header_only(self):
        """An order with no fills should return a CSV with only the header."""
        order = _make_order(fills=[])
        csv_text = generate_keypunch_csv(order)
        rows = _parse_csv(csv_text)
        assert len(rows) == 0
        assert "Trade Date" in csv_text
