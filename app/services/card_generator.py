# =============================================================================
# Card Generator Service
# =============================================================================
# Generates HTML trading cards from order and fill data.
# Cards are self-contained HTML documents with inline CSS for printing.
# =============================================================================

from __future__ import annotations
from datetime import datetime
from app.models.order import Order


def generate_cards_html(order: Order) -> str:
    """Generate the full HTML document for trading cards."""

    trade_date = order.trade_date.strftime("%m/%d/%Y")
    account = order.account or ""
    bk_broker = order.bk_broker or ""

    # Collect legs — always use full order leg volume, not a fill ratio.
    # Card quantities per counterparty are driven by FillCounterparty.quantity,
    # not by a ratio of filled vs total.
    legs = []
    for leg in order.legs:
        is_fut = leg.option_type is None and leg.strike is None
        vol = leg.volume

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
            "side": leg.side,
            "volume": vol,
            "option_type": leg.option_type or "",
            "strike": strike_str,
            "price": str(leg.price) if leg.price else "",
            "mo_code": (leg.mo_card_code or leg.expiry or "").upper(),
            "is_fut": is_fut,
        })

    # Delta ratio for futures card quantities
    total_opt_vol = 0
    total_fut_vol = 0
    for l in legs:
        if l["is_fut"]:
            total_fut_vol = l["volume"]
        elif total_opt_vol == 0:
            total_opt_vol = l["volume"]
    if total_opt_vol == 0:
        total_opt_vol = 1
    delta_ratio = total_fut_vol / total_opt_vol

    # Collect counterparties grouped by bracket + broker
    groups = []  # list of {bracket, broker, cps: [{qty, symbol}]}
    seen = set()
    for fill in order.fills:
        for cp in fill.counterparties:
            key = (cp.bracket or "", cp.broker or "")
            if key not in seen:
                seen.add(key)
                groups.append({
                    "bracket": cp.bracket or "",
                    "broker": cp.broker or "",
                    "cps": [],
                })
            for g in groups:
                if (g["bracket"], g["broker"]) == key:
                    g["cps"].append({
                        "qty": cp.quantity,
                        "symbol": cp.symbol or "",
                    })

    is_multi_leg = len(legs) > 1

    # Build HTML
    html = _card_html_header(trade_date)

    for g in groups:
        bracket_display = g["bracket"] + ("6" if is_multi_leg else "")
        cps = g["cps"]
        pages = max(1, (len(cps) - 1) // 5 + 1)

        for page in range(pages):
            cp_from = page * 5
            cp_to = min(cp_from + 5, len(cps))
            page_cps = cps[cp_from:cp_to]

            for leg in legs:
                html += _build_card(
                    leg, page_cps, bracket_display, g["broker"],
                    trade_date, delta_ratio, account, bk_broker,
                )

    html += "</div></body></html>"
    return html


def _card_html_header(trade_date: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Trading Cards {trade_date}</title>
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
.cards-wrap {{ display:flex; flex-wrap:wrap; gap:0.15in; justify-content:flex-start; padding:0.3in; }}
.card {{ width:3.5in; height:5.5in; border-radius:10px; overflow:hidden; border:1.5px solid;
  page-break-inside:avoid; display:flex; flex-direction:column; }}
.card-header {{ padding:6px 10px 0 10px; flex-shrink:0; }}
.card-top-row {{ display:flex; justify-content:space-between; align-items:baseline; }}
.card-type {{ font-size:19px; font-weight:900; letter-spacing:1px; }}
.card-broker {{ font-size:19px; font-weight:900; letter-spacing:2px; text-align:center; flex:1; }}
.card-bk {{ font-size:12px; font-weight:700; text-align:right; text-transform:uppercase; margin-top:1px; padding-right:2px; }}
.card-acct {{ font-size:10px; font-weight:600; text-align:right; }}
.card-role {{ font-size:12px; font-weight:700; margin-top:2px; padding-bottom:4px; }}
.card-rule {{ border:none; border-top:1px solid; margin:0; flex-shrink:0; }}
.col-headers {{ display:flex; flex-shrink:0; border-bottom:1.5px solid; }}
.col-headers div {{ font-size:11px; font-weight:700; text-align:center; padding:3px 1px; }}
.slots {{ flex:1; display:flex; flex-direction:column; min-height:0; }}
.slot {{ flex:1; display:flex; border-bottom:0.5px solid; min-height:0; }}
.slot:last-child {{ border-bottom:none; }}
.cell {{ display:flex; align-items:center; justify-content:center; font-size:14px;
  border-right:0.5px solid; overflow:hidden; }}
.cell:last-child {{ border-right:none; }}
.cp-cell {{ display:flex; flex-direction:column; border-right:0.5px solid; overflow:hidden; }}
.cp-top {{ flex:1; display:flex; align-items:center; justify-content:center; font-size:14px;
  font-weight:700; color:#007700; border-bottom:0.5px solid; overflow:hidden; }}
.cp-bot {{ flex:1; display:flex; align-items:center; justify-content:center;
  font-size:14px; color:#005500; overflow:hidden; }}
.w-qty {{ width:13%; }} .w-mo {{ width:16%; }} .w-str {{ width:16%; }}
.w-pr {{ width:13%; }} .w-cp {{ width:32%; }} .w-bkt {{ width:10%; }}
@media print {{ .print-nav {{ display:none !important; }}
  body {{ background:white; padding:0; margin:0; }}
  @page {{ size:letter portrait; margin:0.35in; }}
  .cards-wrap {{ gap:0.15in; padding:0; }}
  .card {{ width:3.5in; height:5.5in; border:1.5px solid !important;
    -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
</style></head><body>
<div class='print-nav'>
  <span class='brand'>AXIS TRADE FLOW</span>
  <a href='/orders'>Orders</a>
  <a href='/reports/order-log'>Order Log</a>
  <a href='javascript:history.back()'>← Back</a>
  <button class='print-btn' onclick='window.print()'>Print (Ctrl+P)</button>
</div>
<div class='cards-wrap'>
"""


def _build_card(
    leg: dict, cps: list, bracket: str, broker: str,
    trade_date: str, delta_ratio: float, account: str, bk_broker: str,
) -> str:
    is_fut = leg["is_fut"]

    if is_fut:
        card_type = "FUTURES"
        card_role = "BUYER" if leg["side"] == "B" else "SELLER"
        cp_role = "SELLER" if leg["side"] == "B" else "BUYER"
        bg = "#fefce8"
    elif leg["option_type"] == "C":
        card_type = "CALL"
        card_role = "BUYER" if leg["side"] == "B" else "SELLER"
        cp_role = "SELLER" if leg["side"] == "B" else "BUYER"
        bg = "#ffffff"
    else:
        card_type = "PUT"
        card_role = "BUYER" if leg["side"] == "B" else "SELLER"
        cp_role = "SELLER" if leg["side"] == "B" else "BUYER"
        bg = "#f5f0c8"

    ink = "#1f4e79" if card_role == "BUYER" else "#cc2222"

    q_lbl = "CARS" if is_fut else "QTY."
    s_lbl = "" if is_fut else "STRIKE"
    p_lbl = "PRICE" if is_fut else "PREM."
    b_lbl = "BK" if is_fut else "BKT."

    h = f"<div class='card' style='background:{bg};border-color:{ink};'>\n"
    h += f"<div class='card-header'><div class='card-top-row'>"
    h += f"<div class='card-type' style='color:{ink}'>{card_type}</div>"
    h += f"<div class='card-broker' style='color:{ink}'>{broker}</div>"
    h += f"<div class='card-acct' style='color:{ink}'>{account}</div></div>"
    # BK Broker line - only on futures cards, below and right-aligned
    if is_fut and bk_broker:
        h += f"<div class='card-bk' style='color:{ink}'>BK {bk_broker.upper()}</div>"
    h += f"<div class='card-role' style='color:{ink}'>{card_role}</div></div>"
    h += f"<hr class='card-rule' style='border-color:{ink}'>"

    # Column headers
    h += f"<div class='col-headers' style='border-color:{ink};color:{ink}'>"
    h += f"<div class='w-qty' style='border-right:0.5px solid {ink}'>{q_lbl}</div>"
    h += f"<div class='w-mo' style='border-right:0.5px solid {ink}'>MO.</div>"
    h += f"<div class='w-str' style='border-right:0.5px solid {ink}'>{s_lbl}</div>"
    h += f"<div class='w-pr' style='border-right:0.5px solid {ink}'>{p_lbl}</div>"
    h += f"<div class='w-cp' style='border-right:0.5px solid {ink}'>{cp_role}</div>"
    h += f"<div class='w-bkt'>{b_lbl}</div></div>"

    # Counterparty slots (5 per card)
    h += "<div class='slots'>\n"
    for slot in range(5):
        h += f"<div class='slot' style='border-color:{ink}'>"
        if slot < len(cps):
            cp = cps[slot]
            if is_fut:
                dq = round(cp["qty"] * delta_ratio)
            else:
                dq = cp["qty"]

            # Split symbol on /
            sym = cp["symbol"]
            if "/" in sym:
                s_top, s_bot = sym.split("/", 1)
            else:
                s_top, s_bot = sym, "&nbsp;"

            h += f"<div class='cell w-qty' style='color:{ink};border-color:{ink}'>{dq}</div>"
            h += f"<div class='cell w-mo' style='color:{ink};border-color:{ink}'>{leg['mo_code']}</div>"
            if is_fut:
                h += f"<div class='cell w-str' style='border-color:{ink}'>&nbsp;</div>"
            else:
                h += f"<div class='cell w-str' style='color:{ink};border-color:{ink}'>{leg['strike']}</div>"
            h += f"<div class='cell w-pr' style='color:{ink};border-color:{ink}'>{leg['price']}</div>"
            h += f"<div class='cp-cell w-cp' style='border-color:{ink}'>"
            h += f"<div class='cp-top' style='border-color:{ink}'>{s_top.strip()}</div>"
            h += f"<div class='cp-bot'>{s_bot.strip()}</div></div>"
            h += f"<div class='cell w-bkt' style='color:{ink};border-right:none'>{bracket}</div>"
        else:
            h += f"<div class='cell w-qty' style='border-color:{ink}'>&nbsp;</div>"
            h += f"<div class='cell w-mo' style='border-color:{ink}'>&nbsp;</div>"
            h += f"<div class='cell w-str' style='border-color:{ink}'>&nbsp;</div>"
            h += f"<div class='cell w-pr' style='border-color:{ink}'>&nbsp;</div>"
            h += f"<div class='cp-cell w-cp' style='border-color:{ink}'>"
            h += f"<div class='cp-top' style='border-color:{ink}'>&nbsp;</div>"
            h += f"<div class='cp-bot'>&nbsp;</div></div>"
            h += f"<div class='cell w-bkt' style='border-right:none'>&nbsp;</div>"
        h += "</div>\n"
    h += "</div>"
    h += "</div>\n"
    return h


def build_card_data_snapshot(order: Order) -> dict:
    legs = []
    for leg in order.legs:
        legs.append({
            "leg_index": leg.leg_index,
            "side": leg.side,
            "volume": leg.volume,
            "contract_type": leg.contract_type,
            "expiry": leg.expiry,
            "strike": leg.strike,
            "option_type": leg.option_type,
            "price": leg.price,
            "mo_card_code": leg.mo_card_code,
        })
    fills = []
    for fill in order.fills:
        counterparties = []
        for cp in fill.counterparties:
            counterparties.append({
                "quantity": cp.quantity,
                "broker": cp.broker,
                "symbol": cp.symbol,
                "bracket": cp.bracket,
                "notes": cp.notes,
            })
        fills.append({
            "fill_id": fill.id,
            "fill_quantity": fill.fill_quantity,
            "counterparties": counterparties,
        })
    return {
        "ticket_display": order.ticket_display,
        "trade_date": str(order.trade_date),
        "raw_input": order.raw_input,
        "direction": order.direction,
        "house": order.house,
        "account": order.account,
        "bk_broker": order.bk_broker,
        "legs": legs,
        "fills": fills,
    }