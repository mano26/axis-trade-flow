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


# -----------------------------------------------------------------------------
# Printed tickets / cards: company branding
# -----------------------------------------------------------------------------

SAMPLE_PRINT = (
    "<html><head><title>AXIS Ticket</title><style></style></head><body>"
    "<div class='print-nav'>\n  <span class='brand'>AXIS TRADE FLOW</span>\n</div>"
    "<div class='tkt-header'><div class='tkt-title'>A X I S</div></div>"
    "<div class='tkt-header'><span class='tkt-title'>A X I S</span></div>"
    "<div class='ln-cont-bar'>AXIS &nbsp;·&nbsp; ACCT &nbsp;·&nbsp; PAGE 2 OF 2</div>"
    "</body></html>"
)


def _brand(app, tenant):
    from flask import g
    from app.services.print_branding import apply_print_branding
    with app.test_request_context():
        g.tenant = tenant
        return apply_print_branding(SAMPLE_PRINT)


def test_classic_company_prints_are_untouched(app):
    with app.app_context():
        t = Tenant(name="AXIS", slug="axis")
        assert _brand(app, t) == SAMPLE_PRINT


def test_prints_outside_a_request_are_untouched(app):
    from app.services.print_branding import apply_print_branding
    assert apply_print_branding(SAMPLE_PRINT) == SAMPLE_PRINT


def test_new_design_company_prints_its_own_name(app):
    with app.app_context():
        t = Tenant(name="Roma Ventures", slug="roma", ui_theme="confirmengine")
        html = _brand(app, t)
    assert "A X I S" not in html and "AXIS TRADE FLOW" not in html
    assert html.count("ROMA VENTURES</span>") == 2          # both title styles
    assert "<title>ROMA VENTURES Ticket" in html
    assert "ROMA VENTURES &nbsp;·&nbsp; ACCT" in html        # continuation bar
    assert "ce-print-nav" in html and "Confirm<b>Engine</b>" in html
    assert "class='print-nav ce-print-nav'" in html          # still hidden when printing


def test_brand_name_wins_and_is_escaped(app):
    with app.app_context():
        t = Tenant(name="Roma Ventures", slug="roma", ui_theme="confirmengine",
                   brand_name="R&D <Desk>")
        html = _brand(app, t)
    assert "R&amp;D &lt;DESK&gt;" in html and "<DESK>" not in html


def test_ticket_title_is_centred_by_layout():
    """The title sits in the middle grid column on every ticket layout."""
    from app.services.ticket_generator import _ticket_html_header, _ticket_html_header_cps
    for css in (_ticket_html_header(1), _ticket_html_header_cps(1), _ticket_html_header_cps(1, "linear")):
        compact = css.replace(" ", "")
        assert "grid-template-columns:minmax(0,1fr)minmax(0,1fr)autominmax(0,1fr)minmax(0,1fr)" in compact
        assert "grid-column:3" in compact


# -----------------------------------------------------------------------------
# New-design page layouts (templates/ce/…) and the Companies design switch
# -----------------------------------------------------------------------------

LAYOUT_PAGES = {
    "/orders/": 'id="order-form"',
    "/reports/order-log": 'id="log-table"',
    "/reports/eod-summary": "End-of-day",
    "/admin/lookups": "dropdown lists",
    "/admin/users": "&amp; roles",
}


def test_new_design_company_gets_new_page_layouts(app, world):
    c = _login(app, "admin@roma.com")
    for path, marker in LAYOUT_PAGES.items():
        html = c.get(path).get_data(as_text=True)
        assert 'class="ce-stack"' in html, path
        assert marker in html, path


def test_classic_company_never_gets_new_page_layouts(app, world):
    c = _login(app, "admin@classic.com")
    for path in LAYOUT_PAGES:
        assert 'class="ce-stack"' not in c.get(path).get_data(as_text=True), path


def test_new_orders_page_keeps_every_form_field(app, world):
    """Field names / IDs the order form, its script and the server depend on."""
    html = _login(app, "admin@roma.com").get("/orders/").get_data(as_text=True)
    for needle in ('name="trade_string"', 'id="trade_string"', 'name="house"', 'name="account"',
                   'name="ticket_override"', 'id="is_generic_hidden"', 'id="quarter_tick_hidden"',
                   'id="generic_checkbox"', 'id="order-form"', 'name="csrf_token"',
                   'id="f-ticket"', 'id="f-status"', 'function filterOrders()'):
        assert needle in html, needle


def test_super_admin_design_switch(app, world):
    sup = _login(app, "super@x.com")
    sup.post("/companies/switch", data={"tenant_id": world["modern"]})
    html = sup.get("/companies/").get_data(as_text=True)
    assert "Screen design" in html and f"/companies/{world['classic']}/design" in html

    with app.app_context():
        db.session.get(Tenant, world["classic"]).brand_name = "Keep Me"
        db.session.commit()
    sup.post(f"/companies/{world['classic']}/design", data={"ui_theme": "confirmengine"})
    with app.app_context():
        t = db.session.get(Tenant, world["classic"])
        assert t.ui_theme == "confirmengine" and t.brand_name == "Keep Me"   # nothing else touched
    sup.post(f"/companies/{world['classic']}/design", data={"ui_theme": "bogus"})
    with app.app_context():
        assert db.session.get(Tenant, world["classic"]).ui_theme == "confirmengine"


def test_regular_admin_cannot_use_design_switch(app, world):
    c = _login(app, "admin@classic.com")
    r = c.post(f"/companies/{world['classic']}/design", data={"ui_theme": "confirmengine"})
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(Tenant, world["classic"]).ui_theme == "classic"


def test_order_log_ticket_link_opens_linear_ticket(app, world):
    """Order Log 'Ticket' opens the linear ticket in both designs."""
    for email in ("admin@classic.com", "admin@roma.com"):
        c = _login(app, email)
        with app.app_context():
            o = Order.query.execution_options(skip_tenant_guard=True).filter(
                Order.created_by_id == User.query.filter_by(email=email).first().id).first()
            o.status = OrderStatus.FILLED
            db.session.commit()
            oid = o.id
        html = c.get("/reports/order-log").get_data(as_text=True)
        assert f"/tickets/order/{oid}/generate-with-cps-linear" in html, email
        assert f"/tickets/order/{oid}/generate\"" not in html, email


# -----------------------------------------------------------------------------
# Phase 2: order workflow pages and the parse preview
# -----------------------------------------------------------------------------

def _first_order_id(app, email):
    with app.app_context():
        uid = User.query.filter_by(email=email).first().id
        return Order.query.execution_options(skip_tenant_guard=True).filter_by(created_by_id=uid).first().id


def test_parse_preview_reads_without_saving(app, world):
    c = _login(app, "admin@roma.com")
    with app.app_context():
        before = Order.query.execution_options(skip_tenant_guard=True).count()
    ok = c.get("/orders/parse-preview", query_string={"s": "SFRZ6 96.25 96.50 CS 1.75/2500"}).get_json()
    assert ok["ok"] and ok["strategy"] == "cs" and ok["volume"] == 2500 and ok["price"] == 1.75
    assert ok["quarter_tick"] is False
    qt = c.get("/orders/parse-preview", query_string={"s": "SFRM7 95.75 96.00 PS 1.25/500"}).get_json()
    assert qt["ok"] and qt["quarter_tick"] is True
    bad = c.get("/orders/parse-preview", query_string={"s": "SFRZ6 96.25 96.50 CSS 1.75/2500"}).get_json()
    assert bad["ok"] is False and bad["error"]
    with app.app_context():
        assert Order.query.execution_options(skip_tenant_guard=True).count() == before


def test_parse_preview_requires_login(app, world):
    r = app.test_client().get("/orders/parse-preview", query_string={"s": "SFRZ6 96.25 96.50 CS 1/1"})
    assert r.status_code in (302, 401)


def test_new_design_order_detail_keeps_working_parts(app, world):
    oid = _first_order_id(app, "admin@roma.com")
    html = _login(app, "admin@roma.com").get(f"/orders/{oid}").get_data(as_text=True)
    assert 'class="ce-steps"' in html                         # new layout
    assert 'id="fill_quantity"' in html and 'name="fill_quantity"' in html
    assert f"/orders/{oid}/record-fill" in html
    assert 'id="legs-table"' in html                          # classic legs block reused
    assert 'id="edit-ticket-form"' in html                    # classic ticket form reused
    assert "/generate-with-cps\"" not in html                 # split ticket not offered in new design


def test_classic_order_detail_unchanged_markers(app, world):
    oid = _first_order_id(app, "admin@classic.com")
    html = _login(app, "admin@classic.com").get(f"/orders/{oid}").get_data(as_text=True)
    assert 'class="ce-steps"' not in html and 'id="legs-table"' in html


def test_new_design_edit_pages_post_to_same_endpoints(app, world):
    oid = _first_order_id(app, "admin@roma.com")
    c = _login(app, "admin@roma.com")
    html = c.get(f"/orders/{oid}/modify").get_data(as_text=True)
    assert f'action="/orders/{oid}/modify"' in html and 'name="trade_string"' in html and 'id="ce-diff"' in html


def test_new_design_add_user_keeps_fields(app, world):
    html = _login(app, "admin@roma.com").get("/admin/users/create").get_data(as_text=True)
    for needle in ('name="display_name"', 'name="email"', 'name="password"', 'type="radio" name="role" value="admin"'):
        assert needle in html, needle



# -----------------------------------------------------------------------------
# Header lockup: ConfirmEngine wordmark, company name underneath
# -----------------------------------------------------------------------------

def test_header_shows_wordmark_with_company_below(app, world):
    html = _login(app, "admin@roma.com").get("/orders/").get_data(as_text=True)
    head = html[html.index('class="ce-brand-lockup"'):html.index('class="ce-nav"')]
    assert 'Confirm<span class="ce-accent">Engine</span>' in head
    assert head.index("</a>") < head.index('class="ce-brand-company">Roma Ventures</div>')


def test_header_company_uses_brand_name_and_escapes(app, world):
    with app.app_context():
        t = Tenant.query.filter_by(slug="roma").one()
        t.brand_name = "R&D <Desk>"
        db.session.commit()
    html = _login(app, "admin@roma.com").get("/orders/").get_data(as_text=True)
    assert 'class="ce-brand-company">R&amp;D &lt;Desk&gt;</div>' in html
    assert 'Confirm<span class="ce-accent">Engine</span>' in html


def test_classic_header_unchanged(app, world):
    html = _login(app, "admin@classic.com").get("/orders/").get_data(as_text=True)
    assert "ce-brand-company" not in html
