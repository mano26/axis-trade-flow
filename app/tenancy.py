# =============================================================================
# Tenant Context
# =============================================================================
# Single source of truth for "which company is this request acting on?"
#
# Regular users:   always their own tenant (user.tenant_id). Not overridable.
# Super admins:    the tenant they picked in the company switcher, stored in
#                  the session. Falls back to their home tenant.
#
# Every route and service must call active_tenant_id() instead of reading
# current_user.tenant_id directly. current_user.tenant_id is the user's HOME
# tenant and is only correct for non-super users.
#
# REGULATORY NOTE: A second, independent guard (install_tenant_guard) adds a
# tenant_id filter to every ORM SELECT on tenant-owned models during a
# request. If a route ever forgets its filter_by(tenant_id=...), the query
# still cannot return another firm's rows.
# =============================================================================

from flask import g, session, has_request_context
from flask_login import current_user
from sqlalchemy import event
from sqlalchemy.orm import with_loader_criteria

from app.extensions import db

SESSION_KEY = "active_tenant_id"


def resolve_active_tenant() -> None:
    """
    Populate g.tenant / g.tenant_id for this request. Registered as a
    before_request hook in the app factory, so it runs before every
    blueprint's own before_request (including admin.require_admin).
    """
    from app.models.tenant import Tenant

    g.tenant = None
    g.tenant_id = None
    if not current_user.is_authenticated:
        return

    tenant = None
    if current_user.is_super():
        wanted = session.get(SESSION_KEY)
        if wanted is not None:
            tenant = db.session.get(Tenant, int(wanted))
            if tenant is None:               # company was removed; drop stale key
                session.pop(SESSION_KEY, None)
    if tenant is None:
        tenant = current_user.tenant

    # A regular user whose company was suspended mid-session is logged out.
    # (Login already blocks them; this covers sessions that were open.)
    if not tenant.is_active and not current_user.is_super():
        from flask_login import logout_user
        logout_user()
        return

    g.tenant = tenant
    g.tenant_id = tenant.id


def active_tenant_id() -> int:
    """The tenant_id every query and insert in this request must use."""
    tid = getattr(g, "tenant_id", None)
    if tid is None:
        # Fails closed: never fall back to "no filter".
        raise RuntimeError("active_tenant_id() called with no resolved tenant")
    return tid


def active_tenant():
    return g.tenant


def switch_tenant(tenant_id: int) -> None:
    """Super admins only — caller must check is_super() first."""
    session[SESSION_KEY] = int(tenant_id)


def clear_switch() -> None:
    session.pop(SESSION_KEY, None)


def is_viewing_other_tenant() -> bool:
    return (
        current_user.is_authenticated
        and g.get("tenant_id") is not None
        and g.tenant_id != current_user.tenant_id
    )


def install_tenant_guard(app) -> None:
    """
    Defense in depth: auto-scope ORM SELECTs on tenant-owned models to the
    active tenant while inside an authenticated request.

    Not applied to User (login must look up by email across tenants) or to
    Tenant itself (the switcher lists all companies). Those are scoped
    explicitly in their routes.
    """
    from app.models.order import Order
    from app.models.fill import Fill
    from app.models.lookup import LookupValue
    from app.models.audit import AuditLog

    guarded = (Order, Fill, LookupValue, AuditLog)

    @event.listens_for(db.session, "do_orm_execute")
    def _scope_to_tenant(execute_state):
        if not execute_state.is_select or not has_request_context():
            return
        if execute_state.execution_options.get("skip_tenant_guard"):
            return
        tid = g.get("tenant_id")
        if tid is None:
            return
        for model in guarded:
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.tenant_id == tid,
                    include_aliases=True,
                )
            )
