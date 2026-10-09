# =============================================================================
# Tenant Isolation & Super Admin Company Switching
# =============================================================================
# Verifies that:
#   - regular users can only ever see their own company's data
#   - regular users cannot reach company management or the switcher
#   - a super admin's switch changes which company every screen acts on
#   - users / lookups created while switched land in the switched company
#   - the ORM guard filters even a query that forgot its tenant filter
# =============================================================================

import pytest
from flask import g

from app import create_app
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.lookup import LookupValue, LookupType
from app.models.audit import AuditLog


@pytest.fixture
def app():
    """
    Unlike the other test modules, this one does NOT keep an app context
    pushed for the whole test. Flask-Login caches the logged-in user on `g`,
    and `g` lives on the app context — a shared context would let one test
    client's login leak into another's requests and make isolation tests
    meaningless. Each client request gets its own context, as in production.
    (The fixture is still named `app`, so pytest-flask would push a request
    context; the `autouse` override below opts out of that.)
    """
    app = create_app("testing")
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _push_request_context():
    """Override pytest-flask's autouse fixture of the same name: no shared context."""
    yield


def _order(tenant, user, ticket, raw):
    return Order(
        tenant_id=tenant.id, ticket_number=ticket, ticket_display=f"{ticket:04d}",
        raw_input=raw, direction="B", total_quantity=100, strategy="single",
        status=OrderStatus.OPEN, created_by_id=user.id,
    )


@pytest.fixture
def world(app):
    with app.app_context():
        return _build_world()


def _build_world():
    home = Tenant(name="Home Co", slug="home")
    acme = Tenant(name="Acme", slug="acme")
    db.session.add_all([home, acme])
    db.session.flush()

    sup = User(tenant_id=home.id, email="super@x.com", display_name="Super",
               role=UserRole.ADMIN, is_super_admin=True)
    home_admin = User(tenant_id=home.id, email="admin@home.com",
                      display_name="Home Admin", role=UserRole.ADMIN)
    acme_user = User(tenant_id=acme.id, email="trader@acme.com",
                     display_name="Acme Trader")
    for u in (sup, home_admin, acme_user):
        u.set_password("secret1")
    db.session.add_all([sup, home_admin, acme_user])
    db.session.flush()

    db.session.add_all([
        _order(home, sup, 1, "HOME-ORDER SFRH6 C 96.00"),
        _order(acme, acme_user, 1, "ACME-ORDER SFRH6 P 95.50"),
        LookupValue(tenant_id=home.id, lookup_type=LookupType.FILLING_BROKER, value="HOMEBROKER"),
        LookupValue(tenant_id=acme.id, lookup_type=LookupType.FILLING_BROKER, value="ACMEBROKER"),
        LookupValue(tenant_id=home.id, lookup_type=LookupType.CONTRACT, value="SR3"),
    ])
    db.session.commit()
    return {"home": home.id, "acme": acme.id, "sup": sup.id}


def rows(app, *cols, **filters):
    """Return column tuples straight from the DB, in a fresh app context."""
    with app.app_context():
        stmt = db.select(*cols)
        for k, v in filters.items():
            stmt = stmt.where(getattr(cols[0].class_, k) == v)
        return [tuple(r) for r in db.session.execute(stmt).all()]


def login(client, email):
    return client.post("/login", data={"email": email, "password": "secret1"})


# -------------------------------------------------------------------------

def test_regular_user_sees_only_own_company(app, world):
    c = app.test_client()
    login(c, "trader@acme.com")
    page = c.get("/orders/").get_data(as_text=True)
    assert "ACME-ORDER" in page
    assert "HOME-ORDER" not in page


def test_regular_user_cannot_open_other_company_order(app, world):
    [(home_order_id,)] = rows(app, Order.id, tenant_id=world["home"])
    c = app.test_client()
    login(c, "trader@acme.com")
    assert c.get(f"/orders/{home_order_id}").status_code == 404


def test_tenant_admin_cannot_manage_companies_or_switch(app, world):
    c = app.test_client()
    login(c, "admin@home.com")
    assert c.get("/companies/").status_code == 404
    assert c.post("/companies/switch", data={"tenant_id": world["acme"]}).status_code == 404
    # and their view is unchanged
    assert "ACME-ORDER" not in c.get("/orders/").get_data(as_text=True)


def test_super_admin_switch_changes_every_view(app, world):
    c = app.test_client()
    login(c, "super@x.com")
    assert "HOME-ORDER" in c.get("/orders/").get_data(as_text=True)

    c.post("/companies/switch", data={"tenant_id": world["acme"]})
    orders = c.get("/orders/").get_data(as_text=True)
    assert "ACME-ORDER" in orders and "HOME-ORDER" not in orders

    lookups = c.get("/admin/lookups").get_data(as_text=True)
    assert "ACMEBROKER" in lookups and "HOMEBROKER" not in lookups

    users = c.get("/admin/users").get_data(as_text=True)
    assert "trader@acme.com" in users and "admin@home.com" not in users

    assert "Working inside" in orders  # banner

    # switching back to home clears the override
    c.post("/companies/switch", data={"tenant_id": world["home"]})
    assert "HOME-ORDER" in c.get("/orders/").get_data(as_text=True)


def test_writes_while_switched_land_in_switched_company(app, world):
    c = app.test_client()
    login(c, "super@x.com")
    c.post("/companies/switch", data={"tenant_id": world["acme"]})

    c.post("/admin/users/create", data={
        "email": "new@acme.com", "display_name": "New", "password": "secret1", "role": "user",
    })
    c.post("/admin/lookups/add", data={
        "lookup_type": LookupType.FILLING_BROKER, "value": "NEWBROKER",
    })

    assert rows(app, User.tenant_id, email="new@acme.com") == [(world["acme"],)]
    assert rows(app, LookupValue.tenant_id, value="NEWBROKER") == [(world["acme"],)]


def test_logout_clears_switch(app, world):
    c = app.test_client()
    login(c, "super@x.com")
    c.post("/companies/switch", data={"tenant_id": world["acme"]})
    c.get("/logout")
    login(c, "super@x.com")
    assert "HOME-ORDER" in c.get("/orders/").get_data(as_text=True)


def test_create_company_with_admin_and_shared_lookups_only(app, world):
    c = app.test_client()
    login(c, "super@x.com")
    c.post("/companies/create", data={
        "name": "Bravo Capital", "admin_email": "boss@bravo.com",
        "admin_name": "Boss", "admin_password": "secret1",
        "copy_lookups_from": world["home"],
    })
    [(bravo_id,)] = rows(app, Tenant.id, slug="bravo-capital")
    assert rows(app, User.tenant_id, email="boss@bravo.com") == [(bravo_id,)]

    copied = set(rows(app, LookupValue.lookup_type, LookupValue.value, tenant_id=bravo_id))
    assert (LookupType.CONTRACT, "SR3") in copied
    assert not any(t == LookupType.FILLING_BROKER for t, _ in copied)  # brokers never copied

    # the new company's admin sees an empty book
    c2 = app.test_client()
    login(c2, "boss@bravo.com")
    page = c2.get("/orders/").get_data(as_text=True)
    assert "HOME-ORDER" not in page and "ACME-ORDER" not in page


def test_suspend_company_blocks_login(app, world):
    c = app.test_client()
    login(c, "super@x.com")
    c.post(f"/companies/{world['acme']}/toggle-active")
    c2 = app.test_client()
    resp = login(c2, "trader@acme.com")
    assert resp.status_code == 200                  # login form re-rendered, not redirected in
    assert "suspended" in resp.get_data(as_text=True)
    assert c2.get("/orders/").status_code == 302    # still anonymous


def test_suspend_kicks_out_open_session(app, world):
    trader = app.test_client()
    login(trader, "trader@acme.com")
    assert trader.get("/orders/").status_code == 200
    sup = app.test_client()
    login(sup, "super@x.com")
    sup.post(f"/companies/{world['acme']}/toggle-active")
    assert trader.get("/orders/").status_code == 302


def test_switch_is_audited(app, world):
    c = app.test_client()
    login(c, "super@x.com")
    c.post("/companies/switch", data={"tenant_id": world["acme"]})
    assert rows(app, AuditLog.tenant_id, AuditLog.user_id, action="tenant_switched") == [
        (world["acme"], world["sup"])
    ]


def test_orm_guard_filters_unscoped_query(app, world):
    """Even a query with no tenant filter only returns the active company."""
    with app.test_request_context("/"):
        g.tenant_id = world["acme"]   # what resolve_active_tenant sets
        raws = [o.raw_input for o in Order.query.all()]
        assert raws == ["ACME-ORDER SFRH6 P 95.50"]
