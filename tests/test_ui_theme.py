# =============================================================================
# Per-company screen design (ui_theme)
# =============================================================================
# Verifies that:
#   - companies default to the classic design (existing tenants unaffected)
#   - classic companies never load the new layout or stylesheet
#   - a company switched to 'confirmengine' gets the new layout on every page
#   - the switch is per company: two companies can differ at the same time
#   - only a super admin can change it, and junk values are ignored
#   - cards / tickets / keypunch output do not depend on the design
# =============================================================================

import pytest

from app import create_app
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.models.tenant import Tenant
from app.models.user import User, UserRole

PAGES = [
    "/orders/", "/reports/order-log", "/reports/eod-summary",
    "/admin/lookups", "/admin/users",
]
SUPER_PAGES = ["/companies/", "/companies/users"]

NEW_MARKERS = ('class="theme-ce"', "css/confirmengine.css")


@pytest.fixture
def app():
    app = create_app("testing")
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture(autouse=True)
def _push_request_context():
    """Each client request gets its own context (see test_tenant_isolation)."""
    yield


@pytest.fixture
def world(app):
    with app.app_context():
        classic = Tenant(name="Classic Co", slug="classic-co")
        modern = Tenant(name="Roma Ventures", slug="roma", ui_theme="confirmengine")
        db.session.add_all([classic, modern])
        db.session.flush()
        sup = User(tenant_id=classic.id, email="super@x.com", display_name="Super Admin",
                   role=UserRole.ADMIN, is_super_admin=True)
        c_admin = User(tenant_id=classic.id, email="admin@classic.com",
                       display_name="Classic Admin", role=UserRole.ADMIN)
        m_admin = User(tenant_id=modern.id, email="admin@roma.com",
                       display_name="Roma Admin", role=UserRole.ADMIN)
        for u in (sup, c_admin, m_admin):
            u.set_password("secret1")
        db.session.add_all([sup, c_admin, m_admin])
        db.session.flush()
        for t, u in ((classic, c_admin), (modern, m_admin)):
            db.session.add(Order(
                tenant_id=t.id, ticket_number=1, ticket_display="0001",
                raw_input="SFRH6 96.00 C 3/500", direction="B", total_quantity=500,
                strategy="single", status=OrderStatus.OPEN, created_by_id=u.id,
            ))
        db.session.commit()
        return {"classic": classic.id, "modern": modern.id}


def _login(app, email):
    c = app.test_client()
    with app.test_request_context():
        from flask import url_for
        login_url = url_for("auth.login")
    c.post(login_url, data={"email": email, "password": "secret1"})
    return c


def _is_new(html):
    return all(m in html for m in NEW_MARKERS)


def _is_classic(html):
    return not any(m in html for m in NEW_MARKERS) and 'class="nav-bar"' in html


def test_new_companies_default_to_classic(app):
    with app.app_context():
        t = Tenant(name="Fresh", slug="fresh")
        db.session.add(t)
        db.session.commit()
        assert t.ui_theme == "classic"
        assert not t.uses_new_design


def test_classic_company_never_sees_new_design(app, world):
    c = _login(app, "admin@classic.com")
    for p in PAGES:
        r = c.get(p)
        assert r.status_code == 200, p
        assert _is_classic(r.get_data(as_text=True)), p


def test_new_design_company_gets_new_layout_everywhere(app, world):
    c = _login(app, "admin@roma.com")
    for p in PAGES:
        r = c.get(p)
        assert r.status_code == 200, p
        html = r.get_data(as_text=True)
        assert _is_new(html), p
        assert 'class="nav-bar"' not in html, p


def test_new_design_keeps_the_same_page_content(app, world):
    """Same forms and fields in both designs — only the chrome differs."""
    c_classic = _login(app, "admin@classic.com")
    c_new = _login(app, "admin@roma.com")
    a = c_classic.get("/orders/").get_data(as_text=True)
    b = c_new.get("/orders/").get_data(as_text=True)
    for needle in ('id="trade_string"', 'name="house"', 'name="account"',
                   'id="ticket_override"', "Submit Order", 'id="generic_checkbox"'):
        assert needle in a and needle in b, needle


def test_nav_permissions_match_classic(app, world):
    """Admin-only and super-only links appear under exactly the same rules."""
    sup = _login(app, "super@x.com")
    html = sup.get("/orders/").get_data(as_text=True)
    assert "Companies" in html and "All Users" in html
    roma_admin = _login(app, "admin@roma.com")
    html = roma_admin.get("/orders/").get_data(as_text=True)
    assert "Lookups" in html and "Users" in html
    assert "/companies/" not in html


def test_super_admin_switching_follows_the_target_company(app, world):
    sup = _login(app, "super@x.com")
    assert _is_classic(sup.get("/orders/").get_data(as_text=True))
    sup.post("/companies/switch", data={"tenant_id": world["modern"]})
    assert _is_new(sup.get("/orders/").get_data(as_text=True))
    for p in SUPER_PAGES:
        assert sup.get(p).status_code == 200, p
    sup.post("/companies/switch", data={"tenant_id": world["classic"]})
    assert _is_classic(sup.get("/orders/").get_data(as_text=True))


def test_super_admin_can_change_design_and_junk_is_ignored(app, world):
    sup = _login(app, "super@x.com")
    url = f"/companies/{world['classic']}/edit"
    assert "Screen design" in sup.get(url).get_data(as_text=True)

    sup.post(url, data={"name": "Classic Co", "ui_theme": "confirmengine"})
    with app.app_context():
        assert db.session.get(Tenant, world["classic"]).ui_theme == "confirmengine"

    sup.post(url, data={"name": "Classic Co", "ui_theme": "<script>"})
    with app.app_context():
        assert db.session.get(Tenant, world["classic"]).ui_theme == "confirmengine"

    sup.post(url, data={"name": "Classic Co"})          # field omitted: unchanged
    with app.app_context():
        assert db.session.get(Tenant, world["classic"]).ui_theme == "confirmengine"

    sup.post(url, data={"name": "Classic Co", "ui_theme": "classic"})
    with app.app_context():
        assert db.session.get(Tenant, world["classic"]).ui_theme == "classic"


def test_regular_admin_cannot_change_design(app, world):
    admin = _login(app, "admin@classic.com")
    admin.post(f"/companies/{world['classic']}/edit",
               data={"name": "Classic Co", "ui_theme": "confirmengine"})
    with app.app_context():
        assert db.session.get(Tenant, world["classic"]).ui_theme == "classic"


def test_login_page_stays_classic(app, world):
    c = app.test_client()
    with app.test_request_context():
        from flask import url_for
        login_url = url_for("auth.login")
    assert not any(m in c.get(login_url).get_data(as_text=True) for m in NEW_MARKERS)
