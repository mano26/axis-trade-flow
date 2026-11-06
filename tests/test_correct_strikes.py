# =============================================================================
# Correct Trade String — strike corrections
# =============================================================================
# Admins (company admin or super admin) can correct the contract/expiry AND
# strikes of a filled order. Everything fills, prices and counterparties
# depend on must stay the same, and legs must keep their order so per-leg
# prices stay with the right strike.
# =============================================================================

import re
import pytest

from app import create_app
from app.extensions import db
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.order import Order, OrderStatus
from app.models.fill import FillLegPrice, FillCounterparty
from app.models.audit import AuditLog

CS = "SFRZ6 96.25 96.50 CS 1.75/2500"
FLY = "SFRH7 96.00 96.25 96.50 C FLY 1.25/1000"


@pytest.fixture
def app():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
        t = Tenant(name="Desk", slug="desk")
        db.session.add(t)
        db.session.flush()
        for email, role in (("admin@d.com", UserRole.ADMIN), ("trader@d.com", UserRole.USER)):
            u = User(tenant_id=t.id, email=email, display_name=email, role=role)
            u.set_password("secret1")
            db.session.add(u)
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _push_request_context():
    yield


def _client(app, email):
    c = app.test_client()
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    return c


def _filled_order(app, c, trade, qty, prices):
    """Enter, fill, price and allocate an order; return its id."""
    c.post("/orders/create", data={"trade_string": trade, "quarter_tick_confirmed": "1",
                                   "house": "684", "account": "TJM00684"})
    with app.app_context():
        oid = Order.query.execution_options(skip_tenant_guard=True).order_by(Order.id.desc()).first().id
    r = c.post(f"/fills/order/{oid}/create", data={"fill_quantity": str(qty)})
    fid = re.search(r"/fills/(\d+)/", r.headers["Location"]).group(1)
    c.post(f"/fills/{fid}/prices", data={f"price_{i}": str(p) for i, p in enumerate(prices)})
    c.post(f"/fills/{fid}/counterparties", data={
        "cp_qty_0": str(qty), "cp_broker_0": "GKX", "cp_symbol_0": "OPUS",
        "cp_house_0": "59", "cp_bracket_0": "B"})
    return oid


def _state(app, oid):
    with app.app_context():
        o = db.session.get(Order, oid)
        legs = sorted(o.legs, key=lambda l: l.leg_index)
        prices = sorted((p.leg_index, p.price) for f in o.fills
                        for p in FillLegPrice.query.filter_by(fill_id=f.id).all())
        cps = [(cp.quantity, cp.broker, cp.symbol) for f in o.fills
               for cp in FillCounterparty.query.filter_by(fill_id=f.id).all()]
        return {"raw": o.raw_input, "strikes": [l.strike for l in legs],
                "expiries": [l.expiry for l in legs], "sides": [l.side for l in legs],
                "prices": prices, "cps": cps, "status": o.status}


def _correct(c, oid, new, reason="wrong strike entered"):
    return c.post(f"/orders/{oid}/amend-trade-string",
                  data={"corrected_trade_string": new, "correction_reason": reason})


def test_admin_can_correct_a_strike_and_prices_and_cps_are_kept(app):
    c = _client(app, "admin@d.com")
    oid = _filled_order(app, c, CS, 2500, [3.00, 1.25])
    before = _state(app, oid)
    assert before["status"] in (OrderStatus.FILLED, OrderStatus.AMENDED)

    _correct(c, oid, "SFRZ6 96.25 96.625 CS 1.75/2500")
    after = _state(app, oid)
    assert after["raw"] == "SFRZ6 96.25 96.625 CS 1.75/2500"
    assert sorted(after["strikes"]) == [96.25, 96.625]
    assert after["sides"] == before["sides"]
    assert after["prices"] == before["prices"]      # prices stay with their legs
    assert after["cps"] == before["cps"]            # counterparties untouched

    with app.app_context():
        log = AuditLog.query.execution_options(skip_tenant_guard=True).order_by(AuditLog.id.desc()).first()
        assert "STRIKES:" in (log.notes or "") and "96.5 → 96.625" in log.notes


def test_expiry_correction_still_works(app):
    c = _client(app, "admin@d.com")
    oid = _filled_order(app, c, CS, 2500, [3.00, 1.25])
    before = _state(app, oid)
    _correct(c, oid, "SFRH7 96.25 96.50 CS 1.75/2500", "wrong month")
    after = _state(app, oid)
    assert after["strikes"] == before["strikes"] and after["expiries"] != before["expiries"]


@pytest.mark.parametrize("bad", [
    "SFRZ6 96.25 96.50 CS 2/2500",          # price changed
    "SFRZ6 96.25 96.50 CS 1.75/3000",       # quantity changed
    "SFRZ6 96.25 96.50 PS 1.75/2500",       # option type changed
    "SFRZ6 96.25 96.50 96.75 C FLY 1.75/2500",  # leg count changed
    "SFRZ6 96.25 96.125 CS 1.75/2500",      # corrected strike crosses the other
])
def test_disallowed_corrections_change_nothing(app, bad):
    c = _client(app, "admin@d.com")
    oid = _filled_order(app, c, CS, 2500, [3.00, 1.25])
    before = _state(app, oid)
    _correct(c, oid, bad)
    assert _state(app, oid) == before


def test_fly_middle_strike_cannot_jump_a_wing(app):
    c = _client(app, "admin@d.com")
    oid = _filled_order(app, c, FLY, 1000, [3.0, 2.0, 1.75])
    before = _state(app, oid)
    _correct(c, oid, "SFRH7 96.00 96.75 96.50 C FLY 1.25/1000")   # 96.25 -> 96.75 passes 96.50
    assert _state(app, oid) == before
    _correct(c, oid, "SFRH7 96.00 96.375 96.50 C FLY 1.25/1000")  # stays between the wings: OK
    after = _state(app, oid)
    assert sorted(after["strikes"]) == [96.0, 96.375, 96.5] and after["prices"] == before["prices"]


def test_regular_user_cannot_correct(app):
    admin = _client(app, "admin@d.com")
    oid = _filled_order(app, admin, CS, 2500, [3.00, 1.25])
    before = _state(app, oid)
    trader = _client(app, "trader@d.com")
    assert trader.get(f"/orders/{oid}/correct-trade-string").status_code == 302
    _correct(trader, oid, "SFRZ6 96.25 96.625 CS 1.75/2500")
    assert _state(app, oid) == before


def test_company_admin_sees_the_correct_button(app):
    c = _client(app, "admin@d.com")
    oid = _filled_order(app, c, CS, 2500, [3.00, 1.25])
    assert f"/orders/{oid}/correct-trade-string" in c.get(f"/orders/{oid}").get_data(as_text=True)


def test_straddle_strike_correction_moves_both_legs(app):
    """Both legs share one strike, so both change together — allowed."""
    c = _client(app, "admin@d.com")
    oid = _filled_order(app, c, "SFRH7 96.00 ^ 3/500", 500, [1.75, 1.25])
    before = _state(app, oid)
    _correct(c, oid, "SFRH7 96.125 ^ 3/500")
    after = _state(app, oid)
    assert after["strikes"] == [96.125, 96.125] and after["prices"] == before["prices"]
