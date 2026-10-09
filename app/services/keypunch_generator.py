# -*- coding: utf-8 -*-
# =============================================================================
# Keypunch CSV Generator
# =============================================================================
# Generates the CSV file sent to the third-party clearing system for trade
# keypunching.  One CSV row is produced for every combination of:
#
#   fill × order_leg × counterparty_allocation
#
# Column layout mirrors the clearing system's fixed format.  Static columns
# are hardcoded per the desk's instructions; dynamic columns are derived
# from Fill, Order, OrderLeg, and FillCounterparty records.
#
# EXPIRY FORMAT: The clearing system wants YYYYMM (e.g. "202603" for MAR26).
# We convert from our internal "MMMYY" format (e.g. "MAR26").
#
# TRADED AS: "O" for a single-leg option outright, "S" for everything else
# (spreads, multi-leg structures, any futures leg present).
# =============================================================================

import csv
import io
from typing import Optional

# Month abbreviation → 2-digit number
_MONTH_NUM = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def _expiry_to_yyyymm(expiry: str) -> str:
    """
    Convert 'MMMYY' expiry string to 'YYYYMM' clearing-system format.

    Examples:
        'MAR26' → '202603'
        'DEC27' → '202712'

    Returns the original string unchanged if it cannot be parsed, so the
    CSV still contains something useful rather than crashing.
    """
    if not expiry or len(expiry) < 5:
        return expiry or ""
    mon = expiry[:3].upper()
    yr2 = expiry[3:5]
    num = _MONTH_NUM.get(mon)
    if num is None:
        return expiry  # unknown month abbreviation — pass through
    try:
        year = 2000 + int(yr2)
    except ValueError:
        return expiry
    return f"{year}{num}"


def _fmt_price(price: Optional[float]) -> str:
    """Format a price for the CSV (4 decimal places, blank if None)."""
    if price is None:
        return ""
    return f"{price:.4f}"


def _traded_as(order) -> str:
    """
    Return 'O' for a single outright option, 'S' for everything else.

    Single outright = exactly one leg that is an option (has option_type).
    Any futures leg, or more than one leg, → 'S'.
    """
    legs = list(order.legs)
    if len(legs) == 1 and legs[0].option_type is not None:
        return "O"
    return "S"


def generate_keypunch_csv(order) -> str:
    """
    Generate the keypunch CSV content for *order* as a UTF-8 string.

    Iterates every fill on the order; for each fill iterates every leg;
    for each leg iterates every counterparty allocation.  Returns a string
    suitable for sending as a CSV download.

    Args:
        order: An Order model instance with .fills, .legs already loaded.

    Returns:
        CSV text (str) with a header row followed by one data row per
        fill × leg × counterparty.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # ── Header ──────────────────────────────────────────────────────────────
    writer.writerow([
        "Trade Date",
        "Firm",
        "Firm Exchange",
        "Product Exchange",
        "Product",
        "Contract",
        "P/C",
        "Strike Price",
        "Undly",
        "B/S",
        "QTY",
        "Price",
        "Account",
        "Order",
        "Org",
        "CTI",
        "Traded As",
        "Order Type",
        "Brkr",
        "Opp Firm",
        "Opp Brkr",
        "Time Brkt",
        "Alloc Type",
        "Avg Px Grp ID/Carry Firm",
        "Carry Account",
        "CTR",
        "BK Brkr",
        "In Time",
        "In Time Ind",
        "Brkr Rect",
        "Brkr Rect Ind",
        "Exec Time",
        "Exec Ind",
        "Out Time",
        "Out Time Ind",
    ])

    # ── Static values ────────────────────────────────────────────────────────
    trade_date_str = order.trade_date.strftime("%m/%d/%Y") if order.trade_date else ""
    firm = order.house or ""
    traded_as = _traded_as(order)

    # Order number: use override if set, else AXIS ticket_display
    order_num = (
        order.keypunch_order_override.strip()
        if getattr(order, "keypunch_order_override", None)
        else order.ticket_display
    )

    # ── Rows ─────────────────────────────────────────────────────────────────
    for fill in order.fills:
        stamp_in = fill.stamp_time_in or ""
        stamp_out = fill.stamp_time_out or ""
        bk_broker = order.bk_broker or ""

        for leg in order.legs:
            # Price for this leg from the fill's leg prices
            leg_price = next(
                (lp.price for lp in fill.leg_prices if lp.leg_index == leg.leg_index),
                leg.price,  # fall back to order-level price
            )

            contract_yyyymm = _expiry_to_yyyymm(leg.expiry)
            pc = leg.option_type or ""            # 'C', 'P', or blank
            strike = (
                f"{leg.strike:.4f}" if leg.strike is not None else ""
            )

            for cp in fill.counterparties:
                # Quantity for this leg / CP row:
                # cp.quantity already reflects the fill's allocated qty;
                # we scale by the leg's volume ratio within the order.
                # For CVD futures legs use the explicit futures_quantity if set.
                if (
                    leg.option_type is None  # futures leg
                    and cp.futures_quantity is not None
                ):
                    row_qty = cp.futures_quantity
                else:
                    if order.total_quantity and order.total_quantity > 0:
                        ratio = leg.volume / order.total_quantity
                    else:
                        ratio = 1.0
                    row_qty = round(cp.quantity * ratio)

                writer.writerow([
                    trade_date_str,          # Trade Date
                    firm,                    # Firm
                    "CME",                   # Firm Exchange
                    "CME",                   # Product Exchange
                    leg.contract_type,       # Product  (e.g. SR3, S0)
                    contract_yyyymm,         # Contract (e.g. 202603)
                    pc,                      # P/C
                    strike,                  # Strike Price
                    "",                      # Undly — always blank
                    leg.side,                # B/S
                    row_qty,                 # QTY
                    _fmt_price(leg_price),   # Price
                    order.account or "",     # Account
                    order_num,               # Order
                    "C",                     # Org — always C
                    "4",                     # CTI — always 4
                    traded_as,               # Traded As
                    "L",                     # Order Type — always L
                    cp.broker or "",         # Brkr (Filling Broker)
                    cp.cp_house or "",       # Opp Firm
                    cp.symbol or "",         # Opp Brkr (Counterparty)
                    cp.bracket or "",        # Time Brkt
                    "",                      # Alloc Type — blank
                    "",                      # Avg Px Grp ID/Carry Firm — blank
                    "",                      # Carry Account — blank
                    "",                      # CTR — blank
                    bk_broker,              # BK Brkr
                    stamp_in,                # In Time
                    "",                      # In Time Ind — blank
                    "",                      # Brkr Rect — blank
                    "F",                     # Brkr Rect Ind — always F
                    "",                      # Exec Time — blank
                    "",                      # Exec Ind — blank
                    stamp_out,               # Out Time
                    "",                      # Out Time Ind — blank
                ])

    return output.getvalue()
