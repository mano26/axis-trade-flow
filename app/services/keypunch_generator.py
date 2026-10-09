# -*- coding: utf-8 -*-
# =============================================================================
# Keypunch Excel Generator
# =============================================================================
# Generates the Excel (.xlsx) file sent to the third-party clearing system
# for trade keypunching.  One data row is produced for every combination of:
#
#   fill × order_leg × counterparty_allocation
#
# Column layout mirrors the clearing system's fixed format.  Static columns
# are hardcoded per the desk's instructions; dynamic columns are derived
# from Fill, Order, OrderLeg, and FillCounterparty records.
#
# FORMAT RULES:
#   Trade Date  — YYYYMMDD string (e.g. "20261009")
#   Expiry      — YYYYMM string  (e.g. "202603" for MAR26)
#   Strike/Price — 4 decimal places, zero-padded (e.g. "96.0000", "0.0275")
#   Text IDs    — uppercased (Broker, Opp Firm, Opp Brkr, Account, etc.)
#   Cells       — centered horizontally and vertically
#
# TRADED AS: "O" for a single-leg option outright, "S" for everything else.
# =============================================================================

import io
from typing import Optional

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# Month abbreviation → 2-digit number
_MONTH_NUM = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

# Center alignment used on every data cell
_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=False)

# Header style
_HEADER_FONT = Font(bold=True)
_HEADER_FILL = PatternFill(fill_type="solid", fgColor="D9D9D9")
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center")


def _expiry_to_yyyymm(expiry: str) -> str:
    """
    Convert 'MMMYY' expiry string to 'YYYYMM' clearing-system format.

    Examples:
        'MAR26' → '202603'
        'DEC27' → '202712'

    Returns the original string unchanged if it cannot be parsed, so the
    file still contains something useful rather than crashing.
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
    """Format a price as a 4-decimal string, blank if None."""
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


# Keep the old CSV name importable by existing tests so we don't have to
# rename the test helpers — just delegate to the xlsx generator.
def generate_keypunch_csv(order) -> str:
    """Compatibility shim: returns CSV text (used by unit tests only)."""
    import csv as _csv

    headers = [
        "Trade Date", "Firm", "Firm Exchange", "Product Exchange", "Product",
        "Contract", "P/C", "Strike Price", "Undly", "B/S", "QTY", "Price",
        "Account", "Order", "Org", "CTI", "Traded As", "Order Type", "Brkr",
        "Opp Firm", "Opp Brkr", "Time Brkt", "Alloc Type",
        "Avg Px Grp ID/Carry Firm", "Carry Account", "CTR", "BK Brkr",
        "In Time", "In Time Ind", "Brkr Rect", "Brkr Rect Ind",
        "Exec Time", "Exec Ind", "Out Time", "Out Time Ind",
    ]

    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(headers)

    for row in _iter_rows(order):
        writer.writerow(row)

    return buf.getvalue()


def generate_keypunch_xlsx(order) -> bytes:
    """
    Generate the keypunch Excel workbook for *order* as bytes.

    Returns raw .xlsx bytes suitable for serving as an attachment.
    Cells are centered horizontally and vertically; the header row is bold
    with a light-grey fill.  All text identifier columns are uppercased;
    trade date is YYYYMMDD; strike and price use 4 decimal places.

    Args:
        order: An Order model instance with .fills, .legs already loaded.
    """
    headers = [
        "Trade Date", "Firm", "Firm Exchange", "Product Exchange", "Product",
        "Contract", "P/C", "Strike Price", "Undly", "B/S", "QTY", "Price",
        "Account", "Order", "Org", "CTI", "Traded As", "Order Type", "Brkr",
        "Opp Firm", "Opp Brkr", "Time Brkt", "Alloc Type",
        "Avg Px Grp ID/Carry Firm", "Carry Account", "CTR", "BK Brkr",
        "In Time", "In Time Ind", "Brkr Rect", "Brkr Rect Ind",
        "Exec Time", "Exec Ind", "Out Time", "Out Time Ind",
    ]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Keypunch"
    ws.row_dimensions[1].height = 18

    # ── Header row ──────────────────────────────────────────────────────────
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGN

    # ── Data rows ────────────────────────────────────────────────────────────
    for row_idx, row_values in enumerate(_iter_rows(order), start=2):
        ws.row_dimensions[row_idx].height = 15
        for col_idx, value in enumerate(row_values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = _CENTER
            # Force text format so Excel doesn't mangle date-like strings
            cell.number_format = "@"

    # ── Column widths (rough fit) ────────────────────────────────────────────
    col_widths = [
        10, 8, 14, 16, 10, 10, 5, 12, 6, 5, 6, 8,
        10, 8, 5, 5, 10, 12, 8, 10, 10, 10, 12,
        24, 14, 5, 8, 8, 12, 10, 12, 10, 10, 10, 12,
    ]
    for i, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    # Freeze the header row
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Internal row iterator shared by both generators
# ---------------------------------------------------------------------------

def _iter_rows(order):
    """
    Yield one list of cell values per fill × leg × counterparty.
    All text identifier fields are uppercased here.
    """
    trade_date_str = (
        order.trade_date.strftime("%Y%m%d") if order.trade_date else ""
    )
    firm = (order.house or "").upper()
    traded_as = _traded_as(order)
    account = (order.account or "").upper()

    order_num = (
        (order.keypunch_order_override or "").strip().upper()
        if getattr(order, "keypunch_order_override", None)
        else (order.ticket_display or "")
    )

    for fill in order.fills:
        stamp_in  = fill.stamp_time_in  or ""
        stamp_out = fill.stamp_time_out or ""
        bk_broker = (order.bk_broker or "").upper()

        for leg in order.legs:
            leg_price = next(
                (lp.price for lp in fill.leg_prices
                 if lp.leg_index == leg.leg_index),
                leg.price,
            )

            contract_yyyymm = _expiry_to_yyyymm(leg.expiry)
            pc     = leg.option_type or ""
            strike = f"{leg.strike:.4f}" if leg.strike is not None else ""

            for cp in fill.counterparties:
                if (
                    leg.option_type is None
                    and cp.futures_quantity is not None
                ):
                    row_qty = cp.futures_quantity
                else:
                    if order.total_quantity and order.total_quantity > 0:
                        ratio = leg.volume / order.total_quantity
                    else:
                        ratio = 1.0
                    row_qty = round(cp.quantity * ratio)

                yield [
                    trade_date_str,                    # Trade Date
                    firm,                              # Firm
                    "CME",                             # Firm Exchange
                    "CME",                             # Product Exchange
                    (leg.contract_type or "").upper(), # Product
                    contract_yyyymm,                   # Contract
                    pc,                                # P/C
                    strike,                            # Strike Price
                    "",                                # Undly
                    leg.side,                          # B/S
                    str(row_qty),                      # QTY
                    _fmt_price(leg_price),             # Price
                    account,                           # Account
                    order_num,                         # Order
                    "C",                               # Org
                    "4",                               # CTI
                    traded_as,                         # Traded As
                    "L",                               # Order Type
                    (cp.broker    or "").upper(),      # Brkr
                    (cp.cp_house  or "").upper(),      # Opp Firm
                    (cp.symbol    or "").upper(),      # Opp Brkr
                    (cp.bracket   or "").upper(),      # Time Brkt
                    "",                                # Alloc Type
                    "",                                # Avg Px Grp ID/Carry Firm
                    "",                                # Carry Account
                    "",                                # CTR
                    bk_broker,                         # BK Brkr
                    stamp_in,                          # In Time
                    "",                                # In Time Ind
                    "",                                # Brkr Rect
                    "F",                               # Brkr Rect Ind
                    "",                                # Exec Time
                    "",                                # Exec Ind
                    stamp_out,                         # Out Time
                    "",                                # Out Time Ind
                ]
