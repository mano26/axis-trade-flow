# =============================================================================
# Multi-futures-leg printing — second futures column must reach the ticket
# and keypunch, matching the trade cards.
#
# Regression: SFRM7 … D 37 VS (2X) SFRH7 … D 13 1000@11.25, 350 fill split
# ASB 100 / SHD 250. Trader enters SFRM7 FUT 37/92 and SFRH7 FUT 26/65.
# The ticket previously printed 37/92 on BOTH futures legs.
# =============================================================================

import re
import pytest
from app import create_app
from app.extensions import db
from app.models.tenant import Tenant
from app.models.user import User
from app.models.order import Order
from app.services.ticket_generator import generate_ticket_with_cps_html
from app.services.keypunch_generator import generate_keypunch_csv
from app.services.card_generator import generate_cards_html

TRADE = "SFRM7 95.75 C CVD 95.52 D 37 VS (2X) SFRH7 96.25 C CVD 95.64 D 13 1000 @ 11.25"


@pytest.fixture
def order_with_cps():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
        t = Tenant(name="T", slug="t", brand_name="T")
        db.session.add(t)
        db.session.flush()
        u = User(tenant_id=t.id, email="a@b.c", display_name="A",
                 role="admin", password_hash="x")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    c.post("/orders/create", data={"trade_string": TRADE, "quarter_tick_confirmed": "1"})
    with app.app_context():
        oid = Order.query.first().id
    r = c.post(f"/fills/order/{oid}/create", data={"fill_quantity": "350"})
    fid = re.search(r"/fills/(\d+)/prices", r.headers["Location"]).group(1)
    c.post(f"/fills/{fid}/counterparties", data={
        "cp_qty_0": "100", "cp_broker_0": "SPNC", "cp_symbol_0": "ASB",
        "cp_house_0": "125", "cp_bracket_0": "E",
        "cp_fut_qty_0": "37", "cp_fut_qty_2_0": "26",
        "cp_qty_1": "250", "cp_broker_1": "SPNC", "cp_symbol_1": "SHD",
        "cp_house_1": "690", "cp_bracket_1": "E",
        "cp_fut_qty_1": "92", "cp_fut_qty_2_1": "65",
    })
    with app.app_context():
        yield db.session.get(Order, oid)
        db.drop_all()


def _linear_fut_rows(html):
    rows = re.findall(
        r"<td class='ln-side [^']*'>(\w+)</td><td class='ln-qty'>([\d,]+)</td>"
        r"<td class='ln-contract'>(\w+)</td><td class='ln-strike'></td>"
        r"<td class='ln-type'>FUT</td>.*?<td class='ln-cp'>(\w+)</td>", html)
    return sorted((cp, contract, side, int(q)) for side, q, contract, cp in rows)


def test_linear_ticket_uses_both_futures_columns(order_with_cps):
    html = generate_ticket_with_cps_html(order_with_cps, layout="linear")
    assert _linear_fut_rows(html) == [
        ("ASB", "SFRH7", "SELL", 26),
        ("ASB", "SFRM7", "BUY", 37),
        ("SHD", "SFRH7", "SELL", 65),
        ("SHD", "SFRM7", "BUY", 92),
    ]


def test_keypunch_uses_both_futures_columns(order_with_cps):
    rows = [r.split(",") for r in generate_keypunch_csv(order_with_cps).splitlines()]
    # contract YYYYMM is col 5, side col 9, qty col 10, CP symbol col 20;
    # futures rows have no put/call (col 6) or strike (col 7)
    fut = sorted((r[20], r[5], int(r[10])) for r in rows
                 if len(r) > 20 and r[5].isdigit() and r[6] == "" and r[7] == "")
    assert fut == [("ASB", "202703", 26), ("ASB", "202706", 37),
                   ("SHD", "202703", 65), ("SHD", "202706", 92)]


def test_cards_match_entered_futures(order_with_cps):
    import html as _h
    raw = re.sub(r"<style.*?</style>", " ", generate_cards_html(order_with_cps), flags=re.S)
    text = _h.unescape(re.sub(r"<[^>]+>", " ", raw)).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    assert "37 SFRM7 95.52 ASB" in text and "92 SFRM7 95.52 SHD" in text
    assert "26 SFRH7 95.64 ASB" in text and "65 SFRH7 95.64 SHD" in text


# -----------------------------------------------------------------------------
# Regression: header FUT qty and EOD summary must match the CP-entered futures.
# Computed delta for SFRM7 on a 350 fill is 130 (raw 129.5); trader entered
# 37 + 92 = 129.  The header printed 130 and EOD counted 130.
# -----------------------------------------------------------------------------

def _header_fut_qtys(html):
    """(side, qty, contract) for FUT rows in the BUY/SELL header grid."""
    body = html.split("<div class='tkt-body'>", 1)[1].split("<div class='timestamps'>")[0] \
        if "<div class='tkt-body'>" in html else html
    import html as _h
    text = _h.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"\s+", " ", text)
    return text


@pytest.mark.parametrize("layout", ["linear", None])
def test_header_futures_qty_matches_cp_entries(order_with_cps, layout):
    html = generate_ticket_with_cps_html(order_with_cps, layout=layout)
    text = _header_fut_qtys(html)
    assert "FUT 129 SFRM7" in text, text[:400]
    assert "FUT 91 SFRH7" in text, text[:400]
    assert "FUT 130 SFRM7" not in text


def test_fill_futures_totals_use_cp_entries(order_with_cps):
    from app.services.ticket_generator import fill_futures_totals
    (fill,) = order_with_cps.fills
    assert fill_futures_totals(order_with_cps, fill) == [129, 91]


def test_eod_futures_volume_uses_cp_entries(order_with_cps):
    from app.routes.reports import _order_futures_vol
    ratio = order_with_cps.filled_quantity / order_with_cps.total_quantity
    assert _order_futures_vol(order_with_cps, ratio) == 129 + 91


def test_eod_futures_volume_without_cps_unchanged():
    """Orders with no CP'd fills keep the original proportional calc."""
    from types import SimpleNamespace as NS
    from app.routes.reports import _order_futures_vol
    leg = NS(option_type=None, strike=None, volume=370)
    order = NS(legs=[leg], fills=[NS(counterparties=[])])
    assert _order_futures_vol(order, 0.35) == round(370 * 0.35)
