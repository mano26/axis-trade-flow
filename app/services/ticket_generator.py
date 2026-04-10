# =============================================================================
# Ticket Generator Service
# =============================================================================
# Generates HTML exchange tickets from order and fill data.
# Self-contained HTML with inline CSS for print.
# =============================================================================

from __future__ import annotations
from app.models.order import Order


def generate_ticket_html(order: Order) -> str:
    """Generate the full HTML document for an exchange ticket."""

    account = order.account or ""
    bk_broker = order.bk_broker or ""

    # Collect legs
    legs = []
    for leg in order.legs:
        is_fut = leg.option_type is None and leg.strike is None
        # Proportional volume
        if order.filled_quantity > 0:
            ratio = order.filled_quantity / order.total_quantity
            vol = round(leg.volume * ratio)
        else:
            vol = leg.volume

        if is_fut:
            opt_type = "FUT"
        elif leg.option_type == "C":
            opt_type = "CALL"
        else:
            opt_type = "PUT"

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

        legs.append({
            "side": side_display,
            "opt_type": opt_type,
            "qty": str(vol),
            "mo": (leg.mo_card_code or leg.expiry or "").upper(),
            "strike": strike_str,
            "price": str(leg.price) if leg.price else "",
            "is_fut": is_fut,
        })

    # Determine if futures are on buy side, sell side, or both
    fut_on_buy = any(l["is_fut"] and l["side"] == "BUY" for l in legs)
    fut_on_sell = any(l["is_fut"] and l["side"] == "SELL" for l in legs)

    # Collect bracket and broker
    bracket = ""
    brokers = []
    for fill in order.fills:
        for cp in fill.counterparties:
            if cp.bracket and not bracket:
                bracket = cp.bracket
            if cp.broker and cp.broker not in brokers:
                brokers.append(cp.broker)
    broker_str = " / ".join(brokers)

    # Max rows per type per side
    max_rows = 1
    counts = {"BUY": {"CALL": 0, "PUT": 0, "FUT": 0}, "SELL": {"CALL": 0, "PUT": 0, "FUT": 0}}
    for l in legs:
        counts[l["side"]][l["opt_type"]] += 1
    for side in counts.values():
        for c in side.values():
            if c > max_rows:
                max_rows = c
    if max_rows > 4:
        max_rows = 4

    # Timestamps
    time_in = order.time_in.strftime("%H:%M:%S") if order.time_in else ""
    time_out = order.time_out.strftime("%H:%M:%S") if order.time_out else ""
    mods = ""
    if order.modification_timestamps:
        mods = ", ".join(ts[:8] for ts in order.modification_timestamps)

    html = _ticket_html_header(max_rows)
    html += _build_ticket(
        order.ticket_display, legs, max_rows, bracket, broker_str,
        account, bk_broker, fut_on_buy, fut_on_sell,
        time_in, mods, time_out,
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
    bracket: str, broker: str, account: str, bk_broker: str,
    fut_on_buy: bool, fut_on_sell: bool,
    time_in: str, mods: str, time_out: str,
) -> str:
    h = "<div class='ticket'>\n"

    # Header
    h += "<div class='tkt-header'>"
    h += f"<div class='tkt-num'>{ticket_num}</div>"
    h += "<div class='tkt-title'>A X I S</div>"
    h += f"<div class='tkt-acct'>Account No.<div class='tkt-acct-val'>{account}</div></div>"
    h += "</div>\n"

    # Body: buy side / sell side
    h += "<div class='tkt-body'>\n"
    h += _build_side(legs, "BUY", max_rows, bk_broker if fut_on_buy else "")
    h += _build_side(legs, "SELL", max_rows, bk_broker if fut_on_sell else "")
    h += "</div>\n"

    # Timestamps (once, between body and footer)
    ts_parts = []
    if time_in:
        ts_parts.append(f"IN: {time_in}")
    if mods:
        ts_parts.append(f"MOD: {mods}")
    if time_out:
        ts_parts.append(f"OUT: {time_out}")
    if ts_parts:
        h += f"<div class='timestamps'>{' &nbsp;&nbsp; '.join(ts_parts)}</div>\n"

    # Footer
    h += "<div class='tkt-footer'>\n"
    h += _build_bracket_row(bracket)
    h += "<div class='footer-row'>"
    h += "<div class='footer-section'>"
    h += "<span class='check-box'></span> INITIAL &nbsp;&nbsp;&nbsp;"
    h += "<span class='check-box'></span> CLOSING</div>"
    h += "<div class='slmq-box'>S<br>L<br>M<br>Q</div>"
    h += f"<div style='text-align:center'>"
    h += f"<div class='broker-box'>{broker}</div>"
    h += "<div class='broker-label'>Broker No.</div></div>"
    h += "<div class='footer-section'>"
    h += "<span class='check-box'></span> INITIAL &nbsp;&nbsp;&nbsp;"
    h += "<span class='check-box'></span> CLOSING</div>"
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


def _build_bracket_row(active_bracket: str) -> str:
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
            if l.upper() == active_bracket.upper():
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
    bracket = ""
    brokers = []
    for fill in order.fills:
        for cp in fill.counterparties:
            if cp.bracket and not bracket:
                bracket = cp.bracket
            if cp.broker and cp.broker not in brokers:
                brokers.append(cp.broker)
    return {
        "ticket_number": order.ticket_display,
        "legs": legs,
        "bracket": bracket,
        "broker": " / ".join(brokers),
        "account": order.account,
        "bk_broker": order.bk_broker,
    }