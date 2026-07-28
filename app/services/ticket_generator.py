# -*- coding: utf-8 -*-
# =============================================================================
# Ticket Generator Service
# =============================================================================
# Generates HTML exchange tickets from order and fill data.
# Self-contained HTML with inline CSS for print.
# =============================================================================

from __future__ import annotations
from app.models.order import Order

# ── Display toggles ───────────────────────────────────────────────────────────
# Set to True to re-enable timestamp rows on printed tickets.
SHOW_TIMESTAMPS = False

try:
    from zoneinfo import ZoneInfo
    _EXCHANGE_TZ = ZoneInfo("America/Chicago")
except Exception:
    # tzdata package not installed — fall back to UTC offset
    from datetime import timezone, timedelta
    _EXCHANGE_TZ = timezone(timedelta(hours=-6), "CT")  # CST fallback


def _fmt_ts(dt) -> str:
    """Convert a UTC-aware datetime to Chicago time and format as HH:MM:SS."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_EXCHANGE_TZ).strftime("%H:%M:%S")


def generate_ticket_html(order: Order) -> str:
    """Generate the full HTML document for an exchange ticket.

    Produces one ticket per fill that has counterparties or leg prices.
    For a single fill this is identical to the previous behaviour.
    For multiple partial fills each ticket shows that fill's prices.
    """

    account   = order.account  or ""
    bk_broker = order.bk_broker or ""

    # Normalization base for fill quantity scaling — same logic as card_generator:
    # min non-zero option leg volume = the "1x" unit for ratio scaling.
    opt_vols = [l.volume for l in order.legs
                if not (l.option_type is None and l.strike is None)
                and l.volume and l.volume > 0]
    min_opt_vol = min(opt_vols) if opt_vols else 1

    # Build base leg structure (structure and volumes from order.legs).
    # Prices are overridden per-fill below.
    def _make_legs(fill_price_map=None, fill_quantity=None):
        legs = []
        for leg in order.legs:
            is_fut = leg.option_type is None and leg.strike is None
            opt_type = ("FUT" if is_fut
                        else "CALL" if leg.option_type == "C" else "PUT")
            side_display = "BUY" if leg.side == "B" else "SELL"

            strike_str = ""
            if leg.strike:
                s = str(leg.strike)
                if "." not in s:
                    strike_str = s + ".00"
                elif len(s) - s.index(".") < 3:
                    strike_str = s + "0"
                else:
                    strike_str = s

            # Use fill-specific price when available
            if fill_price_map and leg.leg_index in fill_price_map:
                price_str = str(fill_price_map[leg.leg_index])
            else:
                price_str = str(leg.price) if leg.price else ""

            # Scale quantity to this fill's size using leg ratio.
            # For a 1:1 trade all legs show fill_quantity.
            # For a 1:2 ratio spread the 2x leg shows 2 × fill_quantity.
            # Futures legs keep their stored volume (delta hedge is fixed).
            if fill_quantity is not None and not is_fut and min_opt_vol:
                qty_val = round(fill_quantity * leg.volume / min_opt_vol)
            else:
                qty_val = leg.volume

            legs.append({
                "side": side_display,
                "opt_type": opt_type,
                "qty": str(qty_val),
                "mo": (leg.mo_card_code or leg.expiry or "").upper(),
                "strike": strike_str,
                "price": price_str,
                "is_fut": is_fut,
            })
        return legs

    # Max rows per type per side (same across all fills)
    base_legs = _make_legs()
    max_rows = 1
    counts = {"BUY": {"CALL": 0, "PUT": 0, "FUT": 0},
              "SELL": {"CALL": 0, "PUT": 0, "FUT": 0}}
    for l in base_legs:
        counts[l["side"]][l["opt_type"]] += 1
    for side in counts.values():
        for c in side.values():
            if c > max_rows:
                max_rows = c
    max_rows = min(max_rows, 4)

    fut_on_buy  = any(l["is_fut"] and l["side"] == "BUY"  for l in base_legs)
    fut_on_sell = any(l["is_fut"] and l["side"] == "SELL" for l in base_legs)

    # Modification timestamps (shared across all fills)
    mod_times = []
    if order.modification_timestamps:
        from datetime import datetime, timezone
        for ts in order.modification_timestamps:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                mod_times.append(_fmt_ts(dt))
            except Exception:
                mod_times.append(ts[:8])

    time_in  = _fmt_ts(order.time_in)
    time_out = _fmt_ts(order.time_out)

    html = _ticket_html_header(max_rows)

    fills = order.fills or []
    total_fills = len(fills)

    for fill_num, fill in enumerate(fills, 1):
        # Build price map for this fill
        fill_price_map = ({lp.leg_index: lp.price for lp in fill.leg_prices}
                          if fill.leg_prices else {})

        legs = _make_legs(fill_price_map, fill_quantity=fill.fill_quantity)

        # Brackets and broker from this fill's CPs only
        brackets_seen = []
        brokers = []
        for cp in fill.counterparties:
            if cp.bracket and cp.bracket not in brackets_seen:
                brackets_seen.append(cp.bracket)
            if cp.broker and cp.broker.upper() not in brokers:
                brokers.append(cp.broker.upper())
        broker_str = " / ".join(brokers)

        fill_ts = [_fmt_ts(fill.fill_timestamp)] if fill.fill_timestamp else []

        # Fill indicator shown in header when order has multiple fills
        fill_label = (f"FILL {fill_num} OF {total_fills} — {fill.fill_quantity:,} LOTS"
                      if total_fills > 1 else None)

        html += _build_ticket(
            order.ticket_display, legs, max_rows, brackets_seen, broker_str,
            account, bk_broker, fut_on_buy, fut_on_sell,
            time_in, mod_times, fill_ts, time_out,
            fill_label=fill_label,
        )

    # If the order has no fills at all, print a blank ticket with order prices
    if not fills:
        brackets_seen = []
        brokers = []
        html += _build_ticket(
            order.ticket_display, base_legs, max_rows, brackets_seen, "",
            account, bk_broker, fut_on_buy, fut_on_sell,
            time_in, mod_times, [], time_out,
        )

    html += "</div></body></html>"
    return html


def _ticket_html_header(max_rows: int) -> str:
    # Font sizes scale with row count
    sizes = {1: (14, 24, 20, 13), 2: (12, 22, 18, 12), 3: (10, 20, 16, 11)}
    cF, tF, sF, lF = sizes.get(max_rows, (9, 18, 15, 10))

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>AXIS Ticket</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:Arial,Helvetica,sans-serif; background:#e0e0e0; padding:0; }}
.print-nav {{ background:#001f60; color:white; padding:0 24px; height:48px;
  display:flex; align-items:center; gap:24px; }}
.print-nav a {{ color:rgba(255,255,255,0.8); font-size:13px; font-weight:600; text-decoration:none; }}
.print-nav a:hover {{ color:white; }}
.print-nav .brand {{ font-size:16px; font-weight:900; letter-spacing:3px; color:#f7ff4f; }}
.print-nav .print-btn {{ background:#f7ff4f; color:#001f60; padding:6px 16px; border-radius:4px;
  font-weight:700; font-size:13px; cursor:pointer; border:none; margin-left:auto; }}
.tickets-wrap {{ display:flex; flex-wrap:wrap; gap:0.25in; justify-content:center; padding:0.4in; }}
.ticket {{ width:8in; height:5.5in; border:1.5px solid #000; background:#fff;
  padding:14px 18px; display:flex; flex-direction:column; page-break-inside:avoid; }}
.tkt-header {{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px; }}
.tkt-num {{ font-size:15px; color:#cc2222; font-weight:700; font-family:monospace; }}
.tkt-title {{ font-size:{tF}px; font-weight:900; letter-spacing:5px; text-align:center; flex:1; }}
.tkt-acct {{ text-align:right; font-size:10px; }}
.tkt-acct-val {{ border:1px solid #888; padding:2px 8px; min-width:80px; font-weight:700; font-size:11px; }}
.fill-label {{ font-size:8px; font-weight:700; color:#795548; margin-top:2px; }}
.tkt-body {{ display:flex; flex:1; gap:0; border-top:1.5px solid #000; }}
.tkt-side {{ flex:1; display:flex; flex-direction:column; padding:5px 8px; }}
.tkt-side + .tkt-side {{ border-left:1.5px solid #000; }}
.side-title {{ font-size:{sF}px; font-weight:900; text-align:center; letter-spacing:4px; margin-bottom:3px; }}
.opt-section {{ display:flex; align-items:stretch; margin-bottom:1px; }}
.opt-label {{ font-size:{lF}px; font-weight:700; width:40px; display:flex; align-items:center; flex-shrink:0; }}
.opt-grid {{ flex:1; display:grid; grid-template-columns:1fr 1.3fr 1fr 1fr; }}
.opt-cell-group {{ border:0.5px solid #888; display:flex; flex-direction:column; }}
.opt-entry {{ flex:1; display:flex; align-items:center; justify-content:center;
  font-size:{cF}px; font-weight:600; padding:1px 2px; text-align:center; min-height:18px; }}
.col-hdrs {{ display:flex; margin-left:40px; }}
.col-hdr {{ font-size:7px; font-weight:700; text-align:center; color:#555; padding:0 1px; }}
.col-hdr:nth-child(1){{flex:1}} .col-hdr:nth-child(2){{flex:1.3}}
.col-hdr:nth-child(3){{flex:1}} .col-hdr:nth-child(4){{flex:1}}
.bk-info {{ font-size:{sF}px; font-weight:900; letter-spacing:2px; text-align:center;
  margin-top:auto; padding:4px 0; }}
.con-cxl {{ display:flex; align-items:center; margin-top:3px; }}
.con-cxl-label {{ font-size:10px; font-weight:700; width:40px; line-height:1.1; }}
.con-cxl-arrow {{ font-size:14px; margin-left:4px; }}
.tkt-footer {{ margin-top:auto; padding-top:6px; border-top:1px solid #aaa; text-align:center; }}
.bracket-row {{ display:flex; gap:3px; justify-content:center; flex-wrap:wrap;
  font-size:11px; font-weight:700; margin-bottom:5px; }}
.bkt-letter {{ width:15px; height:15px; display:flex; align-items:center; justify-content:center; }}
.bkt-letter.circled {{ border:2px solid #cc2222; border-radius:50%; color:#cc2222; }}
.footer-row {{ display:flex; align-items:center; justify-content:space-between;
  font-size:9px; margin-top:4px; }}
.footer-section {{ display:flex; align-items:center; gap:10px; }}
.check-box {{ display:inline-block; width:9px; height:9px; border:0.5px solid #888; margin-right:2px; }}
.broker-box {{ border:1px solid #888; padding:2px 12px; font-size:10px;
  text-align:center; min-width:70px; }}
.broker-label {{ font-size:7px; color:#666; }}
.slmq-box {{ display:flex; flex-direction:column; align-items:center;
  font-size:10px; font-weight:700; border:0.5px solid #888; padding:2px 6px; line-height:1.2; }}
.timestamps {{ font-size:9px; color:#333; text-align:center; padding:3px 0; font-weight:600; }}
@media print {{ .print-nav {{ display:none !important; }}
  body {{ background:white; padding:0; margin:0; }}
  @page {{ size:8in 5.5in; margin:0; }}
  .tickets-wrap {{ padding:0; }}
  .ticket {{ width:8in; height:5.5in; border:1.5px solid #000 !important;
    -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
}}
</style></head><body>
<div class='print-nav'>
  <span class='brand'>AXIS TRADE FLOW</span>
  <a href='/orders'>Orders</a>
  <a href='/reports/order-log'>Order Log</a>
  <a href='javascript:history.back()'>← Back</a>
  <button class='print-btn' onclick='window.print()'>Print (Ctrl+P)</button>
</div>
<div class='tickets-wrap'>
"""


def _build_ticket(
    ticket_num: str, legs: list, max_rows: int,
    brackets: list, broker: str, account: str, bk_broker: str,
    fut_on_buy: bool, fut_on_sell: bool,
    time_in: str, mod_times: list, fill_times: list, time_out: str,
    fill_label: str = None,
) -> str:
    h = "<div class='ticket'>\n"

    # Header
    h += "<div class='tkt-header'>"
    h += f"<div class='tkt-num'>{ticket_num}</div>"
    h += "<div class='tkt-title'>A X I S</div>"
    acct_block = f"Account No.<div class='tkt-acct-val'>{account}</div>"
    if fill_label:
        acct_block += f"<div class='fill-label'>{fill_label}</div>"
    h += f"<div class='tkt-acct'>{acct_block}</div>"
    h += "</div>\n"

    # Body: buy side / sell side
    h += "<div class='tkt-body'>\n"
    h += _build_side(legs, "BUY", max_rows, bk_broker if fut_on_buy else "")
    h += _build_side(legs, "SELL", max_rows, bk_broker if fut_on_sell else "")
    h += "</div>\n"

    # Timestamps (once, between body and footer)
    # IN: order entry, FILL: each partial fill, MOD: each modification, OUT: completion
    ts_parts = []
    if time_in:
        ts_parts.append(f"IN: {time_in}")
    for ft in fill_times:
        if ft and ft != time_in:
            ts_parts.append(f"FILL: {ft}")
    for mt in mod_times:
        if mt:
            ts_parts.append(f"MOD: {mt}")
    if time_out:
        ts_parts.append(f"OUT: {time_out}")
    if ts_parts and SHOW_TIMESTAMPS:
        h += f"<div class='timestamps'>{' &nbsp;&nbsp; '.join(ts_parts)}</div>\n"

    # Footer
    h += "<div class='tkt-footer'>\n"
    # For spread trades AND single-option CVD trades, circle "6" per floor
    # convention — matches card generator which uses len(all legs) > 1.
    is_multi_leg = len(legs) > 1
    active_brackets = list(brackets) + (["6"] if is_multi_leg and brackets else [])
    h += _build_bracket_row(active_brackets)
    h += "<div class='footer-row'>"
    h += f"<div style='text-align:center'>"
    h += f"<div class='broker-box'>{broker}</div>"
    h += "<div class='broker-label'>Broker No.</div></div>"
    h += "</div>\n"
    h += "</div>\n"  # tkt-footer
    h += "</div>\n"  # ticket
    return h


def _build_side(
    legs: list, side_name: str, max_rows: int,
    bk_broker: str,
) -> str:
    h = "<div class='tkt-side'>"
    h += f"<div class='side-title'>{side_name}</div>\n"

    h += _build_type_section(legs, side_name, "CALL", max_rows)
    h += "<div class='col-hdrs'>"
    h += "<div class='col-hdr'>QUANTITY</div>"
    h += "<div class='col-hdr'>CONTRACT/MONTH</div>"
    h += "<div class='col-hdr'>STRIKE</div>"
    h += "<div class='col-hdr'>PREMIUM</div></div>\n"
    h += _build_type_section(legs, side_name, "PUT", max_rows)
    h += _build_type_section(legs, side_name, "FUT", max_rows)

    # BK Broker info below options/futures, above CON/CXL
    if bk_broker:
        h += f"<div class='bk-info'>BK {bk_broker}</div>\n"

    h += "<div class='con-cxl'>"
    h += "<div class='con-cxl-label'>CON<br>CXL</div>"
    h += "<div class='con-cxl-arrow'>&#9655;</div></div>"
    h += "</div>\n"
    return h


def _build_type_section(legs: list, side_name: str, type_name: str, max_rows: int) -> str:
    # Collect matching legs
    matched = [l for l in legs if l["side"] == side_name and l["opt_type"] == type_name]
    n = max(max_rows, 1)

    h = "<div class='opt-section'>"
    h += f"<div class='opt-label'>{type_name}</div>"
    h += "<div class='opt-grid'>\n"

    # QTY column
    h += "<div class='opt-cell-group'>"
    for j in range(n):
        val = matched[j]["qty"] if j < len(matched) else "&nbsp;"
        h += f"<div class='opt-entry'>{val}</div>"
    h += "</div>\n"

    # CONTRACT/MONTH column
    h += "<div class='opt-cell-group'>"
    for j in range(n):
        val = matched[j]["mo"] if j < len(matched) else "&nbsp;"
        h += f"<div class='opt-entry'>{val}</div>"
    h += "</div>\n"

    # STRIKE column
    h += "<div class='opt-cell-group'>"
    for j in range(n):
        val = matched[j]["strike"] if j < len(matched) else "&nbsp;"
        if not val:
            val = "&nbsp;"
        h += f"<div class='opt-entry'>{val}</div>"
    h += "</div>\n"

    # PREMIUM column
    h += "<div class='opt-cell-group'>"
    for j in range(n):
        val = matched[j]["price"] if j < len(matched) else "&nbsp;"
        if not val:
            val = "&nbsp;"
        h += f"<div class='opt-entry'>{val}</div>"
    h += "</div>\n"

    h += "</div></div>\n"
    return h


def _build_bracket_row(active_brackets) -> str:
    """
    Render the bracket row. active_brackets may be a list of bracket letters
    (for multiple partial fills in different brackets) or a single string.
    All matching letters are circled.
    """
    if isinstance(active_brackets, str):
        active_set = {active_brackets.upper()} if active_brackets else set()
    else:
        active_set = {b.upper() for b in active_brackets if b}

    letters = [
        "$", "A", "B", "C", "D", "E", "F", "G", "H", "I",
        "J", "K", "L", "M", "N", "O", "P", "Q", " ",
        "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
        "2", "3", "4", "5", "6", "7", "8", "9", "%",
    ]
    h = "<div class='bracket-row'>\n"
    for l in letters:
        if l == " ":
            h += "<div style='width:8px'></div>\n"
        else:
            cls = "bkt-letter"
            if l.upper() in active_set:
                cls += " circled"
            h += f"<div class='{cls}'>{l}</div>\n"
    h += "</div>\n"
    return h


def build_ticket_data_snapshot(order: Order) -> dict:
    legs = []
    for leg in order.legs:
        if leg.option_type is None and leg.strike is None:
            opt_type = "FUT"
        elif leg.option_type == "C":
            opt_type = "CALL"
        else:
            opt_type = "PUT"
        legs.append({
            "side": "BUY" if leg.side == "B" else "SELL",
            "opt_type": opt_type,
            "qty": str(leg.volume),
            "mo": leg.mo_card_code or leg.expiry,
            "strike": f"{leg.strike:.2f}" if leg.strike else "",
            "price": str(leg.price) if leg.price else "",
        })
    brackets = []
    brokers = []
    for fill in order.fills:
        for cp in fill.counterparties:
            if cp.bracket and cp.bracket not in brackets:
                brackets.append(cp.bracket)
            if cp.broker and cp.broker.upper() not in brokers:
                brokers.append(cp.broker.upper())
    return {
        "ticket_number": order.ticket_display,
        "legs": legs,
        "brackets": brackets,
        "broker": " / ".join(brokers),
        "account": order.account,
        "bk_broker": order.bk_broker,
    }

# =============================================================================
# Broker Ticket + CPs Generator
# =============================================================================
# Generates one card per filling broker, each showing the full trade grid
# on page 1 followed by per-leg CP allocations.  CVD trades get a separate
# futures card for each broker.
# =============================================================================

_ROWS_PAGE_1  = 8   # CP rows that fit on page 1 (trade grid takes space)
_ROWS_CONT    = 12  # CP rows on continuation pages




# =============================================================================
# Broker Ticket + CPs Generator
# =============================================================================

_ROWS_PAGE_1 = 8    # CP rows that fit on page 1 (trade grid takes space)
_ROWS_CONT   = 12   # CP rows on continuation pages


def generate_ticket_with_cps_html(order) -> str:
    """Generate per-broker broker cards with CP allocations.

    Each fill that has counterparties gets its own set of cards.
    Multiple partial fills produce separate card sets, each showing
    that fill's prices and counterparties only.
    """
    from collections import defaultdict

    # Only process fills that have counterparties entered
    fills_with_cps = [f for f in order.fills if f.counterparties]
    if not fills_with_cps:
        return generate_ticket_html(order)

    # ── Leg structure from order (same for all fills) ─────────────────
    sorted_legs = sorted(order.legs, key=lambda l: l.leg_index)

    # Normalization base for fill quantity scaling
    _opt_vols_raw = [l.volume for l in sorted_legs
                     if not (l.option_type is None and l.strike is None)
                     and l.volume and l.volume > 0]
    _min_opt_vol_raw = min(_opt_vols_raw) if _opt_vols_raw else 1

    def _leg_dict(leg, fill_price_map=None, fill_quantity=None):
        is_fut = leg.option_type is None and leg.strike is None
        opt_type = "FUT" if is_fut else ("CALL" if leg.option_type == "C" else "PUT")
        side = "BUY" if leg.side == "B" else "SELL"
        s = str(leg.strike) if leg.strike else ""
        if s:
            if "." not in s: s += ".00"
            elif len(s) - s.index(".") < 3: s += "0"
        # Use fill-specific price if available, fall back to leg's stored price
        if fill_price_map and leg.leg_index in fill_price_map:
            price_str = str(fill_price_map[leg.leg_index])
        else:
            price_str = str(leg.price) if leg.price else ""
        # Scale displayed qty to this fill's size; futures keep stored volume
        if fill_quantity is not None and not is_fut and _min_opt_vol_raw:
            qty_val = round(fill_quantity * leg.volume / _min_opt_vol_raw)
        else:
            qty_val = leg.volume
        return {
            "side": side, "opt_type": opt_type,
            "qty": str(qty_val),
            "mo": (leg.mo_card_code or leg.expiry or "").upper(),
            "strike": s,
            "price": price_str,
            "is_fut": is_fut, "volume": leg.volume,
        }

    # Leg classification (structure only — prices applied per fill below)
    _base_leg_dicts = [_leg_dict(l) for l in sorted_legs]
    option_dicts    = [d for d in _base_leg_dicts if not d["is_fut"]]
    futures_dicts   = [d for d in _base_leg_dicts if d["is_fut"]]

    _buy_opt  = [d for d in option_dicts if d["side"] == "BUY"]
    _sell_opt = [d for d in option_dicts if d["side"] == "SELL"]

    _all_opt_vols = [d["volume"] for d in option_dicts] or [1]
    _min_opt_vol  = min(_all_opt_vols)

    buy_vol  = (sum(d["volume"] for d in _buy_opt)  / len(_buy_opt))  if _buy_opt  else 0
    sell_vol = (sum(d["volume"] for d in _sell_opt) / len(_sell_opt)) if _sell_opt else 0

    # ── CVD mode detection ────────────────────────────────────────────
    has_buy_options    = buy_vol  > 0
    has_sell_options   = sell_vol > 0
    is_simple_cvd      = bool(futures_dicts) and not (has_buy_options and has_sell_options)
    needs_futures_card = bool(futures_dicts) and not is_simple_cvd

    if is_simple_cvd and futures_dicts:
        _fut_side    = futures_dicts[0]["side"]
        _opt_side    = "BUY" if has_buy_options else "SELL"   # derived from actual option legs
        _opt_vol     = buy_vol if _opt_side == "BUY" else sell_vol
    else:
        _fut_side = _opt_side = _opt_vol = None

    max_rows = 1
    for side in ("BUY", "SELL"):
        for typ in ("CALL", "PUT", "FUT"):
            cnt = sum(1 for d in _base_leg_dicts if d["side"] == side and d["opt_type"] == typ)
            max_rows = max(max_rows, cnt)
    max_rows = min(max_rows, 4)

    bk_broker = order.bk_broker or ""
    total_qty = _min_opt_vol
    is_multi_leg = len(option_dicts) > 1 or bool(futures_dicts)  # bracket+6 for spreads and CVD

    total_fills = len(fills_with_cps)
    html = _ticket_html_header_cps(max_rows)

    for fill_num, fill in enumerate(fills_with_cps, 1):
        # Build price map for this specific fill
        fill_price_map = {lp.leg_index: lp.price for lp in fill.leg_prices} \
                         if fill.leg_prices else {}

        # Build leg dicts with this fill's prices and fill-scaled quantities
        all_leg_dicts = [_leg_dict(l, fill_price_map, fill_quantity=fill.fill_quantity)
                         for l in sorted_legs]
        # Re-derive futures_dicts with prices for this fill
        fill_futures_dicts = [d for d in all_leg_dicts if d["is_fut"]]

        # Timestamps for this fill only
        fill_ts = _fmt_ts(fill.fill_timestamp) if fill.fill_timestamp else ""
        time_in = _fmt_ts(order.time_in)
        if SHOW_TIMESTAMPS and (time_in or fill_ts):
            ts_parts = []
            if time_in: ts_parts.append(f"IN: {time_in}")
            if fill_ts: ts_parts.append(f"FILL: {fill_ts}")
            ts_html = f"<div class='timestamps'>{'&nbsp;&nbsp; '.join(ts_parts)}</div>\n"
        else:
            ts_html = ""

        # Fill label shown on every card when there are multiple fills
        fill_label = (f"FILL {fill_num} OF {total_fills} — {fill.fill_quantity:,} LOTS"
                      if total_fills > 1 else None)

        # Group this fill's CPs by broker
        broker_cps: dict = defaultdict(list)
        for cp in fill.counterparties:
            broker = (cp.broker or "UNKNOWN").upper()
            broker_cps[broker].append(cp)

        for broker, cps in broker_cps.items():
            n_cps = len(cps)
            option_pages = 1 + max(0, (n_cps - _ROWS_PAGE_1 + _ROWS_CONT - 1) // _ROWS_CONT
                                   if n_cps > _ROWS_PAGE_1 else 0)
            futures_pages = 1 if needs_futures_card else 0
            total_pages   = option_pages + futures_pages

            page_num = 1

            # Page 1
            batch = cps[:_ROWS_PAGE_1]
            more  = n_cps > _ROWS_PAGE_1
            rng   = f"1\u2013{len(batch)} of {n_cps}" if more else None
            html += _cps_page1(order, broker, batch, rng, all_leg_dicts,
                               fill_futures_dicts, buy_vol, sell_vol, ts_html,
                               page_num, total_pages, not more, max_rows, total_qty,
                               is_simple_cvd=is_simple_cvd,
                               simple_cvd_opt_side=_opt_side,
                               simple_cvd_opt_vol=_opt_vol,
                               simple_cvd_fut_side=_fut_side,
                               bk_broker=bk_broker,
                               fill_label=fill_label,
                               is_multi_leg=is_multi_leg)
            page_num += 1

            # Continuation pages
            offset = _ROWS_PAGE_1
            while offset < n_cps:
                batch = cps[offset: offset + _ROWS_CONT]
                start, end = offset + 1, offset + len(batch)
                is_last = end >= n_cps
                html += _cps_cont_page(order, broker, batch,
                                       f"{start}\u2013{end} of {n_cps}",
                                       buy_vol, sell_vol,
                                       page_num, total_pages, is_last, total_qty,
                                       is_simple_cvd=is_simple_cvd,
                                       simple_cvd_opt_side=_opt_side,
                                       simple_cvd_opt_vol=_opt_vol,
                                       simple_cvd_fut_side=_fut_side,
                                       fill_label=fill_label,
                                       is_multi_leg=is_multi_leg)
                page_num += 1
                offset   += _ROWS_CONT

            if needs_futures_card:
                html += _cps_futures_page(order, broker, cps, fill_futures_dicts,
                                          page_num, total_pages, total_qty, bk_broker,
                                          fill_label=fill_label)

    html += "</div></body></html>"
    return html

    # ── Classify legs ─────────────────────────────────────────────────
    sorted_legs = sorted(order.legs, key=lambda l: l.leg_index)

    def _leg_dict(leg):
        is_fut = leg.option_type is None and leg.strike is None
        opt_type = "FUT" if is_fut else ("CALL" if leg.option_type == "C" else "PUT")
        side = "BUY" if leg.side == "B" else "SELL"
        s = str(leg.strike) if leg.strike else ""
        if s:
            if "." not in s: s += ".00"
            elif len(s) - s.index(".") < 3: s += "0"
        return {
            "side": side, "opt_type": opt_type,
            "qty": str(leg.volume),
            "mo": (leg.mo_card_code or leg.expiry or "").upper(),
            "strike": s,
            "price": str(leg.price) if leg.price else "",
            "is_fut": is_fut, "volume": leg.volume,
        }

    all_leg_dicts = [_leg_dict(l) for l in sorted_legs]
    option_dicts  = [d for d in all_leg_dicts if not d["is_fut"]]
    futures_dicts = [d for d in all_leg_dicts if d["is_fut"]]

    _buy_opt  = [d for d in option_dicts if d["side"] == "BUY"]
    _sell_opt = [d for d in option_dicts if d["side"] == "SELL"]

    # Use AVERAGE per-leg volume per side, normalised to the minimum option
    # leg volume.  This prevents overcounting on multi-leg structures like
    # a STUPID iron condor (4 buy legs × 100 summing to 400 when the correct
    # per-side qty is 100), and also handles GENERIC orders where legs are
    # entered at fill level rather than full order size.
    _all_opt_vols = [d["volume"] for d in option_dicts] or [1]
    _min_opt_vol  = min(_all_opt_vols)

    buy_vol  = (sum(d["volume"] for d in _buy_opt)  / len(_buy_opt))  if _buy_opt  else 0
    sell_vol = (sum(d["volume"] for d in _sell_opt) / len(_sell_opt)) if _sell_opt else 0

    # ── CVD mode detection ────────────────────────────────────────────
    # Simple CVD: all option legs on one side + a futures hedge.
    # e.g. SELL CALL + BUY FUT.  One card suffices; fold futures qty
    # into the CP table alongside the option qty.
    #
    # Spread CVD: options on BOTH sides + futures (e.g. PS + CVD).
    # Generates a separate futures card per broker.
    has_buy_options  = buy_vol  > 0
    has_sell_options = sell_vol > 0
    is_simple_cvd    = bool(futures_dicts) and not (has_buy_options and has_sell_options)
    needs_futures_card = bool(futures_dicts) and not is_simple_cvd

    # For simple CVD, identify which side carries options vs futures
    if is_simple_cvd and futures_dicts:
        _fut_side = futures_dicts[0]["side"]          # "BUY" or "SELL"
        _opt_side = "SELL" if _fut_side == "BUY" else "BUY"
        _opt_vol  = sell_vol if _opt_side == "SELL" else buy_vol
    else:
        _fut_side = _opt_side = _opt_vol = None

    max_rows = 1
    for side in ("BUY", "SELL"):
        for typ in ("CALL", "PUT", "FUT"):
            cnt = sum(1 for d in all_leg_dicts if d["side"] == side and d["opt_type"] == typ)
            max_rows = max(max_rows, cnt)
    max_rows = min(max_rows, 4)

    # Timestamps
    time_in   = _fmt_ts(order.time_in)
    time_out  = _fmt_ts(order.time_out)
    fill_times = [_fmt_ts(f.fill_timestamp) for f in order.fills if f.fill_timestamp]
    mod_times  = []
    if order.modification_timestamps:
        from datetime import datetime, timezone
        for ts in order.modification_timestamps:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                mod_times.append(_fmt_ts(dt))
            except Exception:
                mod_times.append(ts[:8])

    ts_parts = []
    if time_in: ts_parts.append(f"IN: {time_in}")
    for ft in fill_times:
        if ft and ft != time_in: ts_parts.append(f"FILL: {ft}")
    for mt in mod_times:
        if mt: ts_parts.append(f"MOD: {mt}")
    if time_out: ts_parts.append(f"OUT: {time_out}")
    ts_html = (f"<div class='timestamps'>{'&nbsp;&nbsp; '.join(ts_parts)}</div>\n"
               if ts_parts and SHOW_TIMESTAMPS else "")

    bk_broker = order.bk_broker or ""
    total_qty = _min_opt_vol  # normalise to base leg, not raw order.total_quantity

    html = _ticket_html_header_cps(max_rows)

    for broker, cps in broker_cps.items():
        n_cps = len(cps)
        option_pages = 1 + max(0, (n_cps - _ROWS_PAGE_1 + _ROWS_CONT - 1) // _ROWS_CONT
                               if n_cps > _ROWS_PAGE_1 else 0)
        futures_pages = 1 if needs_futures_card else 0
        total_pages   = option_pages + futures_pages

        page_num = 1

        # Page 1
        batch = cps[:_ROWS_PAGE_1]
        more  = n_cps > _ROWS_PAGE_1
        rng   = f"1\u2013{len(batch)} of {n_cps}" if more else None
        html += _cps_page1(order, broker, batch, rng, all_leg_dicts,
                           futures_dicts, buy_vol, sell_vol, ts_html,
                           page_num, total_pages, not more, max_rows, total_qty,
                           is_simple_cvd=is_simple_cvd,
                           simple_cvd_opt_side=_opt_side,
                           simple_cvd_opt_vol=_opt_vol,
                           simple_cvd_fut_side=_fut_side,
                           bk_broker=bk_broker)
        page_num += 1

        # Continuation pages
        offset = _ROWS_PAGE_1
        while offset < n_cps:
            batch = cps[offset: offset + _ROWS_CONT]
            start, end = offset + 1, offset + len(batch)
            is_last = end >= n_cps
            html += _cps_cont_page(order, broker, batch,
                                   f"{start}\u2013{end} of {n_cps}",
                                   buy_vol, sell_vol,
                                   page_num, total_pages, is_last, total_qty,
                                   is_simple_cvd=is_simple_cvd,
                                   simple_cvd_opt_side=_opt_side,
                                   simple_cvd_opt_vol=_opt_vol,
                                   simple_cvd_fut_side=_fut_side)
            page_num += 1
            offset   += _ROWS_CONT

        # Futures card — spread CVD only
        if needs_futures_card:
            html += _cps_futures_page(order, broker, cps, futures_dicts,
                                      page_num, total_pages, total_qty, bk_broker)

    html += "</div></body></html>"
    return html


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cps_mini_header(order, broker, page_num, total_pages, fill_label=None) -> str:
    date_str = order.trade_date.strftime("%Y/%m/%d")
    h  = "<div class='tkt-header'>"
    h += f"<div class='tkt-num'>#{order.ticket_display}</div>"
    h += "<div class='tkt-title'>A X I S</div>"
    pg_line = f"PAGE {page_num} OF {total_pages}"
    if fill_label:
        pg_line += f"<br><span class='fill-label'>{fill_label}</span>"
    h += (f"<div class='tkt-meta'>{date_str}<br>"
          f"<span class='tkt-pg'>{pg_line}</span></div>")
    h += "</div>\n"
    h += "<div class='tkt-acct-row'>"
    h += f"<div>Acct: <span>{order.account or ''}</span></div>"
    h += f"<div>House: <span>{order.house or ''}</span></div>"
    h += f"<div>Order: <span>#{order.ticket_display}</span></div>"
    h += "</div>\n"
    return h


def _cp_qty_for_leg(cp_qty, leg_vol, total_qty):
    """Scale a counterparty package quantity to a single leg's volume."""
    if not total_qty:
        return 0
    return round(cp_qty * leg_vol / total_qty)


def _cp_half_table(cps, leg_vol, total_qty, cp_range=None,
                   show_hdr=False, hdr_label="BUY",
                   is_futures=False, qty_fn=None, is_multi_leg=False) -> str:
    """
    One half of the split CP section (BUY side or SELL side).
    Columns: □ | QTY | COUNTERPARTY | HOUSE | BKT

    qty_fn: optional callable(cp) -> int that overrides the default
            quantity calculation (used for simple CVD futures half).
    is_multi_leg: when True, appends "6" to the bracket per floor convention.
    """
    h = "<div class='cp-half'>\n"
    if show_hdr:
        h += f"<div class='cp-half-title'>{hdr_label}</div>\n"
    if cp_range:
        h += f"<div class='cp-range'>{cp_range}</div>\n"
    h += "<table class='cp-table'>\n"
    h += ("<thead><tr>"
          "<th></th><th>QTY</th><th>COUNTERPARTY</th><th>HOUSE</th><th>BKT</th>"
          "</tr></thead>\n<tbody>\n")

    total = 0
    for cp in cps:
        sym_raw = cp.symbol or ""
        if cp.cp_house:
            cp_sym   = sym_raw.upper()
            cp_house = cp.cp_house.upper()
        elif "/" in sym_raw:
            cp_sym   = sym_raw.split("/")[0].strip().upper()
            cp_house = sym_raw.split("/")[1].strip().upper()
        else:
            cp_sym   = sym_raw.upper()
            cp_house = ""

        bracket = (cp.bracket or "").upper()
        bracket_display = bracket + ("6" if is_multi_leg and bracket else "")

        if qty_fn is not None:
            qty = qty_fn(cp)
        elif is_futures:
            qty = cp.futures_quantity or 0
        else:
            qty = _cp_qty_for_leg(cp.quantity or 0, leg_vol, total_qty)

        total += qty
        qty_str = str(qty) if qty else "&nbsp;"
        h += (f"<tr>"
              f"<td><span class='cp-chk'></span></td>"
              f"<td class='cp-qty'>{qty_str}</td>"
              f"<td>{cp_sym}</td><td>{cp_house}</td><td>{bracket_display}</td>"
              f"</tr>\n")

    h += "</tbody></table>\n"
    if total:
        h += f"<div class='cp-total'>TOTAL: {total:,}</div>\n"
    h += "</div>\n"
    return h


def _cp_split_section(cps, buy_vol, sell_vol, total_qty,
                      cp_range=None, show_hdrs=False, is_multi_leg=False) -> str:
    """
    Full split CP section: BUY half | vertical line | SELL half.
    Sides with no option legs (vol=0) are omitted entirely; the
    remaining side expands to full width automatically.
    On page 1 show_hdrs=False (context from trade grid above).
    On page 2+ show_hdrs=True.
    """
    h = "<div class='cp-section'>\n"
    if buy_vol > 0:
        h += _cp_half_table(cps, buy_vol, total_qty, cp_range,
                            show_hdr=show_hdrs, hdr_label="BUY",
                            is_multi_leg=is_multi_leg)
    if sell_vol > 0:
        h += _cp_half_table(cps, sell_vol, total_qty, cp_range,
                            show_hdr=show_hdrs, hdr_label="SELL",
                            is_multi_leg=is_multi_leg)
    h += "</div>\n"
    return h


def _cp_cvd_half_table(cps, opt_vol, total_qty, cp_range=None,
                       show_hdr=False, hdr_label="BUY",
                       is_multi_leg=False) -> str:
    """CVD half-table: □ | OPT | FUT | COUNTERPARTY | HOUSE | BKT."""
    h = "<div class='cp-half'>\n"
    if show_hdr:
        h += f"<div class='cp-half-title'>{hdr_label}</div>\n"
    if cp_range:
        h += f"<div class='cp-range'>{cp_range}</div>\n"
    h += "<table class='cp-table'>\n"
    h += ("<thead><tr>"
          "<th></th><th>OPT</th><th>FUT</th>"
          "<th>COUNTERPARTY</th><th>HOUSE</th><th>BKT</th>"
          "</tr></thead>\n<tbody>\n")

    opt_total = fut_total = 0
    for cp in cps:
        sym_raw = cp.symbol or ""
        if cp.cp_house:
            cp_sym, cp_house = sym_raw.upper(), cp.cp_house.upper()
        elif "/" in sym_raw:
            cp_sym   = sym_raw.split("/")[0].strip().upper()
            cp_house = sym_raw.split("/")[1].strip().upper()
        else:
            cp_sym, cp_house = sym_raw.upper(), ""

        bracket = (cp.bracket or "").upper()
        bracket_display = bracket + ("6" if is_multi_leg and bracket else "")

        opt_qty = _cp_qty_for_leg(cp.quantity or 0, opt_vol, total_qty)
        fut_qty = cp.futures_quantity or 0
        opt_total += opt_qty
        fut_total += fut_qty

        h += (f"<tr>"
              f"<td><span class='cp-chk'></span></td>"
              f"<td class='cp-qty'>{opt_qty if opt_qty else '&nbsp;'}</td>"
              f"<td class='cp-qty'>{fut_qty if fut_qty else '&nbsp;'}</td>"
              f"<td>{cp_sym}</td><td>{cp_house}</td><td>{bracket_display}</td>"
              f"</tr>\n")

    h += "</tbody></table>\n"
    parts = []
    if opt_total: parts.append(f"OPT: {opt_total:,}")
    if fut_total: parts.append(f"FUT: {fut_total:,}")
    if parts:
        h += f"<div class='cp-total'>TOTAL &nbsp; {'&nbsp;|&nbsp;'.join(parts)}</div>\n"
    h += "</div>\n"
    return h


def _cp_simple_cvd_section(cps, opt_side, opt_vol, fut_side,
                            total_qty, cp_range=None, show_hdrs=False,
                            is_multi_leg=False) -> str:
    """
    CVD CP section: each leg appears on its correct trade side.

    Same-side CVD (BUY PUT+FUT or SELL PUT+FUT):
      → Combined OPT+FUT table on that side, other side empty.

    Opposite-side CVD (SELL CALL+BUY FUT or BUY CALL+SELL FUT):
      → Option qty on opt_side, futures qty on fut_side, standard half-tables.
    """
    def _fut_qty_fn(cp):
        return cp.futures_quantity or 0

    h = "<div class='cp-section'>\n"

    if opt_side == fut_side:
        # Both legs on the same side — combined OPT+FUT half on that side
        cvd  = _cp_cvd_half_table(cps, opt_vol, total_qty, cp_range,
                                   show_hdr=show_hdrs, hdr_label=opt_side,
                                   is_multi_leg=is_multi_leg)
        empty = "<div class='cp-half'></div>\n"
        h += (cvd + empty) if opt_side == "BUY" else (empty + cvd)
    else:
        # Opposite sides — options on opt_side, futures on fut_side
        opt_half = _cp_half_table(cps, opt_vol, total_qty, cp_range,
                                   show_hdr=show_hdrs, hdr_label=opt_side,
                                   is_multi_leg=is_multi_leg)
        fut_half = _cp_half_table(cps, 0, total_qty, cp_range,
                                   show_hdr=show_hdrs, hdr_label=fut_side,
                                   qty_fn=_fut_qty_fn, is_multi_leg=is_multi_leg)
        # Always BUY on left, SELL on right
        if opt_side == "BUY":
            h += opt_half + fut_half   # BUY=opt, SELL=fut
        else:
            h += fut_half + opt_half   # BUY=fut, SELL=opt

    h += "</div>\n"
    return h


def _cps_footer_html(broker, bk_broker="") -> str:
    h  = "<div class='tkt-footer'>\n"
    bk = f"BK: {bk_broker}" if bk_broker else ""
    h += f"<div class='bk-info'>{bk}</div>"
    h += (f"<div class='broker-footer'>"
          f"<div class='broker-box'>{' '.join(broker)}</div>"
          f"<div class='broker-box-label'>FILLING BROKER</div></div>\n")
    h += "</div>\n"
    return h


def _cps_page1(order, broker, cps, cp_range, all_leg_dicts, futures_dicts,
               buy_vol, sell_vol, ts_html,
               page_num, total_pages, is_last, max_rows, total_qty,
               is_simple_cvd=False, simple_cvd_opt_side=None,
               simple_cvd_opt_vol=None, simple_cvd_fut_side=None,
               bk_broker="", fill_label=None, is_multi_leg=False) -> str:
    h  = "<div class='ticket'>\n"
    h += _cps_mini_header(order, broker, page_num, total_pages, fill_label)
    h += "<div class='tkt-body'>\n"
    h += _build_side(all_leg_dicts, "BUY",  max_rows, "")
    h += _build_side(all_leg_dicts, "SELL", max_rows, "")
    h += "</div>\n"
    h += ts_html

    if is_simple_cvd and simple_cvd_opt_side:
        h += _cp_simple_cvd_section(
            cps, simple_cvd_opt_side, simple_cvd_opt_vol,
            simple_cvd_fut_side, total_qty,
            cp_range=cp_range, show_hdrs=False, is_multi_leg=is_multi_leg,
        )
    else:
        h += _cp_split_section(cps, buy_vol, sell_vol, total_qty,
                               cp_range=cp_range, show_hdrs=False,
                               is_multi_leg=is_multi_leg)

    if is_last:
        h += _cps_footer_html(broker, bk_broker if is_simple_cvd else "")
    h += "</div>\n"
    return h


def _cps_cont_page(order, broker, cps, cp_range,
                   buy_vol, sell_vol,
                   page_num, total_pages, is_last, total_qty,
                   is_simple_cvd=False, simple_cvd_opt_side=None,
                   simple_cvd_opt_vol=None, simple_cvd_fut_side=None,
                   fill_label=None, is_multi_leg=False) -> str:
    h  = "<div class='ticket'>\n"
    h += _cps_mini_header(order, broker, page_num, total_pages, fill_label)

    if is_simple_cvd and simple_cvd_opt_side:
        h += _cp_simple_cvd_section(
            cps, simple_cvd_opt_side, simple_cvd_opt_vol,
            simple_cvd_fut_side, total_qty,
            cp_range=cp_range, show_hdrs=True, is_multi_leg=is_multi_leg,
        )
    else:
        h += _cp_split_section(cps, buy_vol, sell_vol, total_qty,
                               cp_range=cp_range, show_hdrs=True,
                               is_multi_leg=is_multi_leg)

    if is_last:
        h += _cps_footer_html(broker)
    h += "</div>\n"
    return h


def _cps_futures_page(order, broker, cps, futures_dicts,
                      page_num, total_pages, total_qty, bk_broker,
                      fill_label=None) -> str:
    fut = futures_dicts[0]
    h  = "<div class='ticket'>\n"
    h += _cps_mini_header(order, broker, page_num, total_pages, fill_label)

    h += "<div class='tkt-body'>\n"
    h += f"<div class='tkt-side'><div class='side-title'>{fut['side']}</div>"
    h += "<div class='opt-section'><div class='opt-label'>FUT</div><div class='opt-grid'>"
    for val in [fut["qty"], fut["mo"], "&nbsp;", fut["price"] or "&nbsp;"]:
        h += f"<div class='opt-cell-group'><div class='opt-entry'>{val}</div></div>"
    h += "</div></div>"
    h += "<div class='col-hdrs'>"
    for lbl in ["QUANTITY", "CONTRACT/MONTH", "&nbsp;", "PRICE"]:
        h += f"<div class='col-hdr'>{lbl}</div>"
    h += "</div>"
    h += "<div class='con-cxl'><div class='con-cxl-label'>CON<br>CXL</div>"
    h += "<div style='font-size:12px;margin-left:4px'>&#9655;</div></div>"
    h += "</div>"
    h += "<div class='tkt-side' style='border-left:1.5px solid #000'></div>"
    h += "</div>\n"

    h += "<div class='cp-section'>\n"
    h += _cp_half_table(cps, 0, total_qty, show_hdr=True,
                        hdr_label=fut["side"], is_futures=True)
    h += "</div>\n"

    h += _cps_footer_html(broker, bk_broker)
    h += "</div>\n"
    return h


def _ticket_html_header_cps(max_rows: int) -> str:
    sizes = {1: (14, 24, 20, 13), 2: (12, 22, 18, 12), 3: (10, 20, 16, 11)}
    cF, tF, sF, lF = sizes.get(max_rows, (9, 18, 15, 10))
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>AXIS Ticket + CPs</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,Helvetica,sans-serif;background:#e0e0e0;padding:0}}
.print-nav{{background:#001f60;color:white;padding:0 24px;height:48px;
  display:flex;align-items:center;gap:24px}}
.print-nav a{{color:rgba(255,255,255,0.8);font-size:13px;font-weight:600;text-decoration:none}}
.print-nav .brand{{font-size:16px;font-weight:900;letter-spacing:3px;color:#f7ff4f}}
.print-nav .print-btn{{background:#f7ff4f;color:#001f60;padding:6px 16px;border-radius:4px;
  font-weight:700;font-size:13px;cursor:pointer;border:none;margin-left:auto}}
.tickets-wrap{{display:flex;flex-wrap:wrap;gap:0.25in;justify-content:center;padding:0.4in}}
.ticket{{width:8in;min-height:3.5in;border:1.5px solid #000;background:#fff;
  padding:10px 14px;display:flex;flex-direction:column;page-break-inside:avoid}}
.tkt-header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}}
.tkt-num{{font-size:13px;color:#cc2222;font-weight:700;font-family:monospace}}
.tkt-title{{font-size:{tF}px;font-weight:900;letter-spacing:5px;text-align:center;flex:1}}
.tkt-meta{{text-align:right;font-size:9px;font-weight:600;line-height:1.5}}
.tkt-pg{{font-size:9px;font-weight:700;color:#555}}
.fill-label{{font-size:8px;font-weight:700;color:#795548;letter-spacing:0.5px}}
.tkt-acct-row{{display:flex;justify-content:space-between;font-size:9px;margin-bottom:4px;
  padding-bottom:3px;border-bottom:1px solid #ccc}}
.tkt-acct-row span{{font-weight:700}}
.tkt-body{{display:flex;flex:0 0 auto;border-top:1.5px solid #000}}
.tkt-side{{flex:1;display:flex;flex-direction:column;padding:4px 6px}}
.tkt-side+.tkt-side{{border-left:1.5px solid #000}}
.side-title{{font-size:{sF}px;font-weight:900;text-align:center;letter-spacing:4px;margin-bottom:2px}}
.opt-section{{display:flex;align-items:stretch;margin-bottom:1px}}
.opt-label{{font-size:9px;font-weight:700;width:34px;display:flex;align-items:center;flex-shrink:0}}
.opt-grid{{flex:1;display:grid;grid-template-columns:1fr 1.3fr 1fr 1fr}}
.opt-cell-group{{border:0.5px solid #888;display:flex;flex-direction:column}}
.opt-entry{{flex:1;display:flex;align-items:center;justify-content:center;
  font-size:{cF}px;font-weight:600;padding:1px 2px;text-align:center;min-height:18px}}
.col-hdrs{{display:flex;margin-left:34px}}
.col-hdr{{font-size:7px;font-weight:700;text-align:center;color:#555;padding:0 1px}}
.col-hdr:nth-child(1){{flex:1}}.col-hdr:nth-child(2){{flex:1.3}}
.col-hdr:nth-child(3){{flex:1}}.col-hdr:nth-child(4){{flex:1}}
.con-cxl{{display:flex;align-items:center;margin-top:3px}}
.con-cxl-label{{font-size:9px;font-weight:700;width:34px;line-height:1.1}}
.timestamps{{font-size:8px;color:#333;text-align:center;padding:3px 0;font-weight:600;
  border-top:1px solid #ddd;border-bottom:1px solid #ddd;margin:3px 0}}
/* Split CP section */
.cp-section{{display:flex;border-top:1.5px solid #000;flex:1}}
.cp-half{{flex:1;padding:4px 6px;display:flex;flex-direction:column}}
.cp-half+.cp-half{{border-left:1.5px solid #000}}
.cp-half-title{{font-size:11px;font-weight:900;letter-spacing:3px;text-align:center;
  border-bottom:1px solid #000;padding-bottom:3px;margin-bottom:3px}}
.cp-range{{font-size:7.5px;font-weight:600;color:#666;text-align:center;margin-bottom:3px}}
.cp-table{{width:100%;border-collapse:collapse;font-size:9px}}
.cp-table th{{font-size:7.5px;font-weight:700;text-align:left;padding:2px 3px;
  color:#444;border-bottom:0.5px solid #888}}
.cp-table td{{padding:2px 3px;border-bottom:0.5px solid #eee;font-weight:600;vertical-align:middle}}
.cp-table tr:last-child td{{border-bottom:none}}
.cp-qty{{text-align:right;font-family:monospace}}
.cp-chk{{display:inline-block;width:8px;height:8px;border:0.5px solid #666;vertical-align:middle}}
.cp-total{{font-size:7.5px;font-weight:700;color:#333;text-align:right;margin-top:3px;
  padding-top:2px;border-top:0.5px solid #ccc}}
/* Footer */
.tkt-footer{{display:flex;justify-content:space-between;align-items:flex-end;
  margin-top:6px;padding-top:4px;border-top:1px solid #ccc}}
.broker-footer{{display:flex;flex-direction:column;align-items:center}}
.broker-box{{border:1.5px solid #000;padding:4px 20px;font-size:14px;font-weight:900;
  letter-spacing:4px;text-align:center;min-width:110px}}
.broker-box-label{{font-size:7px;font-weight:700;color:#666;text-align:center;
  letter-spacing:1px;margin-top:2px}}
.bk-info{{font-size:9px;font-weight:700;color:#333}}
@media print{{
  .print-nav{{display:none !important}}
  body{{background:white;padding:0;margin:0}}
  @page{{size:8in 5.5in;margin:0}}
  .tickets-wrap{{padding:0}}
  .ticket{{width:8in;page-break-inside:avoid;
    -webkit-print-color-adjust:exact;print-color-adjust:exact}}
}}
</style></head><body>
<div class='print-nav'>
  <span class='brand'>AXIS TRADE FLOW</span>
  <a href='/orders'>Orders</a>
  <a href='/reports/order-log'>Order Log</a>
  <a href='javascript:history.back()'>&#8592; Back</a>
  <button class='print-btn' onclick='window.print()'>Print (Ctrl+P)</button>
</div>
<div class='tickets-wrap'>
"""