# =============================================================================
# Print branding — company name + ConfirmEngine header on tickets and cards
# =============================================================================
# Ticket and card pages are built as complete HTML documents by
# ticket_generator / card_generator. This module rebrands a finished document
# for the company that is printing it.
#
# Classic companies: the document is returned UNCHANGED (byte for byte), so
# their printed tickets stay exactly as they were.
#
# New-design companies (tenant.ui_theme == 'confirmengine'):
#   - the on-screen toolbar becomes the ConfirmEngine header (it is hidden
#     when printing, as before)
#   - the printed title "A X I S" becomes the company's own name
#     (brand_name if set, otherwise the company name), with a small
#     "ConfirmEngine" logo line under it
#   - the page <title> and continuation-page bar use the company name too
# Layout, grids, fonts and everything else on the ticket are untouched.
# =============================================================================

import re
from html import escape

from flask import g, has_app_context

_NAV_RE = re.compile(r"<div class='print-nav'>.*?</div>", re.S)
_TITLE_RE = re.compile(r"<(div|span) class='tkt-title'>A X I S</\1>")

def _mark(size: int, ink: str = "#0E1726", red: str = "#F2542D", acc: str = "#2456E6") -> str:
    import math

    def pt(t):
        r = math.radians(t)
        return 80 + 50 * math.sin(r), 80 - 50 * math.cos(r)

    segs = ""
    for a0, a1 in ((137, 187), (196, 246), (255, 305), (314, 350)):
        (x0, y0), (x1, y1) = pt(a0), pt(a1)
        segs += (f"<path d='M{x0:.1f} {y0:.1f}A50 50 0 0 1 {x1:.1f} {y1:.1f}' fill='none' "
                 f"stroke='{ink}' stroke-width='14'/>")
    (x0, y0), (x1, y1) = pt(359), pt(403)
    segs += (f"<path d='M{x0:.1f} {y0:.1f}A50 50 0 0 1 {x1:.1f} {y1:.1f}' fill='none' "
             f"stroke='{red}' stroke-width='14'/>")
    return (f"<svg viewBox='0 0 160 160' width='{size}' height='{size}' aria-hidden='true'>"
            f"{segs}"
            f"<path d='M50 62H66M48 80H60M50 98H66' stroke='{ink}' stroke-width='8' stroke-linecap='round'/>"
            f"<path d='M80 80L99 50' stroke='{acc}' stroke-width='7' stroke-linecap='round'/>"
            f"<circle cx='80' cy='80' r='9' fill='{ink}'/></svg>")


def _branded_tenant():
    """The active tenant if it uses the new design, else None."""
    if not has_app_context():
        return None
    tenant = getattr(g, "tenant", None)
    if tenant is None or not getattr(tenant, "uses_new_design", False):
        return None
    return tenant


def company_title(tenant) -> str:
    return ((tenant.brand_name or tenant.name or "").strip() or "AXIS").upper()


_CSS = """<style>
/* ConfirmEngine print header (screen only; hidden by each page's @media print) */
.print-nav.ce-print-nav{background:#fff;color:#0E1726;height:60px;padding:0 20px;gap:6px;
  border-bottom:1px solid #E6E8EE;font-family:Inter,system-ui,-apple-system,'Segoe UI',Arial,sans-serif}
.ce-print-nav .ce-brand{display:flex;align-items:center;gap:9px;margin-right:14px;
  font-size:17px;font-weight:600;letter-spacing:-0.02em;color:#0E1726;text-decoration:none}
.ce-print-nav .ce-brand b{color:#2456E6;font-weight:600}
.ce-print-nav a.ce-link{color:#3A4354;font-size:14px;font-weight:500;padding:8px 12px;border-radius:999px}
.ce-print-nav a.ce-link:hover{background:rgba(14,23,38,0.05);color:#0E1726}
.ce-print-nav .ce-co{margin-left:auto;display:flex;align-items:center;gap:8px;height:34px;padding:0 12px;
  border:1px solid #E1E4EB;border-radius:999px;font-size:13px;font-weight:500}
.ce-print-nav .ce-co i{width:8px;height:8px;border-radius:999px;background:#1E9C8F;display:inline-block}
.ce-print-nav .print-btn{margin-left:8px;background:#0E1726;color:#fff;border-radius:999px;
  padding:9px 18px;font-size:14px;font-weight:500;border:0;cursor:pointer;font-family:inherit}
.ce-print-nav .print-btn:hover{background:#1E2A40}
/* Company title on the ticket itself */
.tkt-title.ce-tkt-title{letter-spacing:0;padding-left:0;line-height:1.05}
.ce-tkt-name{display:block;letter-spacing:0.28em;padding-left:0.28em}
.ce-tkt-title.ce-long .ce-tkt-name{font-size:0.82em}
.ce-tkt-by{display:flex;align-items:center;justify-content:center;gap:3px;margin-top:2px;
  font-size:9px;font-weight:700;letter-spacing:0.02em;color:#444;text-transform:none}
.ce-tkt-by b{color:#2456E6;font-weight:700}
</style>
"""


def apply_print_branding(html: str) -> str:
    """Rebrand a finished ticket/card document for the active company."""
    tenant = _branded_tenant()
    if tenant is None:
        return html   # classic: untouched

    title = company_title(tenant)
    safe_title = escape(title)
    safe_name = escape(tenant.name or title)

    nav = (
        "<div class='print-nav ce-print-nav'>"
        f"<a class='ce-brand' href='/orders'>{_mark(28)}<span>Confirm<b>Engine</b></span></a>"
        "<a class='ce-link' href='/orders'>Orders</a>"
        "<a class='ce-link' href='/reports/order-log'>Order Log</a>"
        "<a class='ce-link' href='javascript:history.back()'>&#8592; Back</a>"
        f"<span class='ce-co'><i></i>{safe_name}</span>"
        "<button class='print-btn' onclick='window.print()'>Print (Ctrl+P)</button>"
        "</div>"
    )
    long_cls = " ce-long" if len(title) > 10 else ""
    title_inner = (
        f"<span class='ce-tkt-name'>{safe_title}</span>"
        f"<span class='ce-tkt-by'>{_mark(14)}Confirm<b>Engine</b></span>"
    )

    html = _NAV_RE.sub(lambda m: nav, html, count=1)
    html = _TITLE_RE.sub(
        lambda m: f"<{m.group(1)} class='tkt-title ce-tkt-title{long_cls}'>{title_inner}</{m.group(1)}>",
        html,
    )
    html = html.replace("<title>AXIS Ticket", f"<title>{safe_title} Ticket", 1)
    html = html.replace("AXIS &nbsp;·&nbsp;", f"{safe_title} &nbsp;·&nbsp;")
    html = html.replace("</head>", _CSS + "</head>", 1)
    return html
