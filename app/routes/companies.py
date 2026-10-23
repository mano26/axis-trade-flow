# =============================================================================
# Company (Tenant) Management — Super Admin Only
# =============================================================================
# Create companies, edit/suspend them, and switch the active company.
# URL prefix: /companies
#
# User management inside a company reuses the existing /admin/users screens:
# switch to the company, then manage its users there. Those routes already
# scope by active_tenant_id(), so they act on whichever company is active.
#
# REGULATORY NOTE: Every company create/edit/suspend and every switch is
# written to the audit log against the affected tenant.
# =============================================================================

import re

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, abort,
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.lookup import LookupValue, LookupType
from app.services import audit_service
from app.tenancy import switch_tenant, clear_switch

companies_bp = Blueprint("companies", __name__)

# Lookup types that describe the market rather than the firm. These can be
# copied from an existing company when a new one is created. Brokers,
# counterparties, houses, accounts and brackets are firm-specific and are
# never copied.
SHAREABLE_LOOKUPS = [
    LookupType.MARKET, LookupType.CONTRACT,
    LookupType.EXPIRY, LookupType.OPTION_TYPE,
]


@companies_bp.before_request
@login_required
def require_super():
    if not current_user.is_super():
        abort(404)   # don't advertise that this area exists


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100]


# -------------------------------------------------------------------------
# Switcher
# -------------------------------------------------------------------------

@companies_bp.route("/switch", methods=["POST"])
def switch():
    tenant = db.session.get(Tenant, request.form.get("tenant_id", type=int))
    if tenant is None:
        flash("Unknown company.", "danger")
        return redirect(request.referrer or url_for("orders.index"))

    if tenant.id == current_user.tenant_id:
        clear_switch()
    else:
        switch_tenant(tenant.id)

    audit_service.log_action(
        action="tenant_switched", entity_type="tenant",
        entity_id=tenant.id, tenant_id=tenant.id,
        notes=f"Super admin {current_user.email} switched into {tenant.name}",
    )
    db.session.commit()
    flash(f"Now working in {tenant.name}.", "info")
    # Always land on a list page: the referrer may be an order from the
    # previous company, which correctly 404s in the new one.
    return redirect(url_for("orders.index"))


# -------------------------------------------------------------------------
# Company CRUD
# -------------------------------------------------------------------------

@companies_bp.route("/")
def company_list():
    tenants = Tenant.query.order_by(Tenant.name).all()
    user_counts = dict(
        db.session.query(User.tenant_id, db.func.count(User.id))
        .group_by(User.tenant_id).all()
    )
    return render_template(
        "companies/list.html", tenants=tenants, user_counts=user_counts,
    )


@companies_bp.route("/create", methods=["GET", "POST"])
def create_company():
    sources = Tenant.query.order_by(Tenant.name).all()
    if request.method == "GET":
        return render_template("companies/form.html", tenant=None, sources=sources)

    name = request.form.get("name", "").strip()
    slug = _slugify(request.form.get("slug", "").strip() or name)
    brand_name = request.form.get("brand_name", "").strip() or None
    admin_email = request.form.get("admin_email", "").strip().lower()
    admin_name = request.form.get("admin_name", "").strip()
    admin_password = request.form.get("admin_password", "")
    copy_from = request.form.get("copy_lookups_from", type=int)

    errors = []
    if not name or not slug:
        errors.append("Company name is required.")
    if slug and Tenant.query.filter_by(slug=slug).first():
        errors.append(f"Slug '{slug}' is already taken.")
    if admin_email:
        if not admin_name or len(admin_password) < 6:
            errors.append("First admin needs a name and a password of 6+ characters.")
        if User.query.filter_by(email=admin_email).first():
            errors.append(f"A user with email {admin_email} already exists.")
    if errors:
        for e in errors:
            flash(e, "warning")
        return render_template(
            "companies/form.html", tenant=None, sources=sources, form=request.form,
        )

    tenant = Tenant(name=name, slug=slug, brand_name=brand_name)
    db.session.add(tenant)
    db.session.flush()

    copied = 0
    if copy_from:
        src_rows = (
            LookupValue.query
            .execution_options(skip_tenant_guard=True)   # reading another tenant on purpose
            .filter(LookupValue.tenant_id == copy_from,
                    LookupValue.lookup_type.in_(SHAREABLE_LOOKUPS),
                    LookupValue.is_active.is_(True))
            .all()
        )
        for row in src_rows:
            db.session.add(LookupValue(
                tenant_id=tenant.id, lookup_type=row.lookup_type,
                value=row.value, sort_order=row.sort_order,
            ))
            copied += 1

    if admin_email:
        admin = User(
            tenant_id=tenant.id, email=admin_email,
            display_name=admin_name, role=UserRole.ADMIN,
        )
        admin.set_password(admin_password)
        db.session.add(admin)

    audit_service.log_action(
        action="tenant_created", entity_type="tenant",
        entity_id=tenant.id, tenant_id=tenant.id,
        after_value={"name": name, "slug": slug, "brand_name": brand_name,
                     "first_admin": admin_email or None,
                     "lookups_copied_from": copy_from, "lookups_copied": copied},
    )
    db.session.commit()

    flash(f"Company '{name}' created.", "success")
    if request.form.get("switch_now"):
        switch_tenant(tenant.id)
        return redirect(url_for("admin.lookup_list"))
    return redirect(url_for("companies.company_list"))


@companies_bp.route("/<int:tenant_id>/edit", methods=["GET", "POST"])
def edit_company(tenant_id):
    tenant = db.get_or_404(Tenant, tenant_id)
    if request.method == "GET":
        return render_template("companies/form.html", tenant=tenant, sources=[])

    before = {"name": tenant.name, "brand_name": tenant.brand_name}
    name = request.form.get("name", "").strip()
    if not name:
        flash("Company name is required.", "warning")
        return redirect(url_for("companies.edit_company", tenant_id=tenant.id))
    tenant.name = name
    tenant.brand_name = request.form.get("brand_name", "").strip() or None
    audit_service.log_action(
        action="tenant_updated", entity_type="tenant",
        entity_id=tenant.id, tenant_id=tenant.id,
        before_value=before,
        after_value={"name": tenant.name, "brand_name": tenant.brand_name},
    )
    db.session.commit()
    flash(f"Company '{tenant.name}' updated.", "success")
    return redirect(url_for("companies.company_list"))


@companies_bp.route("/<int:tenant_id>/toggle-active", methods=["POST"])
def toggle_active(tenant_id):
    tenant = db.get_or_404(Tenant, tenant_id)
    if tenant.id == current_user.tenant_id:
        flash("You can't suspend your own home company.", "warning")
        return redirect(url_for("companies.company_list"))
    tenant.is_active = not tenant.is_active
    audit_service.log_action(
        action="tenant_activated" if tenant.is_active else "tenant_suspended",
        entity_type="tenant", entity_id=tenant.id, tenant_id=tenant.id,
        before_value={"is_active": not tenant.is_active},
        after_value={"is_active": tenant.is_active},
    )
    db.session.commit()
    flash(f"'{tenant.name}' {'reactivated' if tenant.is_active else 'suspended'}.", "info")
    return redirect(url_for("companies.company_list"))


# -------------------------------------------------------------------------
# Cross-tenant User Management
# -------------------------------------------------------------------------
# These routes let a super admin manage ANY user from a single panel,
# without having to switch companies first. Role changes and hard deletes
# are only available here, not in the regular /admin/users panel.

@companies_bp.route("/users")
def user_list():
    """All users across all companies."""
    users = (
        User.query
        .join(Tenant, User.tenant_id == Tenant.id)
        .order_by(Tenant.name, User.display_name)
        .all()
    )
    tenants = Tenant.query.order_by(Tenant.name).all()
    return render_template("companies/users.html", users=users, tenants=tenants)


@companies_bp.route("/users/create", methods=["GET", "POST"])
def create_user():
    """Create a user under any tenant."""
    tenants = Tenant.query.filter_by(is_active=True).order_by(Tenant.name).all()
    if request.method == "POST":
        tenant_id = request.form.get("tenant_id", type=int)
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        role = request.form.get("role", UserRole.USER)
        password = request.form.get("password", "").strip()

        errors = []
        if not tenant_id or not db.session.get(Tenant, tenant_id):
            errors.append("Company is required.")
        if not email:
            errors.append("Email is required.")
        if not display_name:
            errors.append("Display name is required.")
        if role not in UserRole.ALL:
            errors.append("Invalid role.")
        if not password or len(password) < 6:
            errors.append("Password must be at least 6 characters.")
        if email and User.query.filter_by(email=email).first():
            errors.append(f"Email '{email}' is already registered.")

        if errors:
            for e in errors:
                flash(e, "warning")
            return render_template("companies/user_form.html", tenants=tenants,
                                   form=request.form)

        user = User(
            tenant_id=tenant_id, email=email,
            display_name=display_name, role=role, is_active_user=True,
        )
        user.set_password(password)
        db.session.add(user)
        audit_service.log_action(
            action="user_created", entity_type="user",
            entity_id=0, tenant_id=tenant_id,
            after_value={"email": email, "role": role, "display_name": display_name},
        )
        db.session.commit()
        flash(f"User '{display_name}' created.", "success")
        return redirect(url_for("companies.user_list"))

    return render_template("companies/user_form.html", tenants=tenants, form={})


@companies_bp.route("/users/<int:user_id>/role", methods=["POST"])
def change_role(user_id):
    """Change a user's role."""
    user = db.get_or_404(User, user_id)
    if user.is_super_admin:
        flash("Cannot change a super admin's role.", "warning")
        return redirect(url_for("companies.user_list"))
    new_role = request.form.get("role", "")
    if new_role not in UserRole.ALL:
        flash("Invalid role.", "warning")
        return redirect(url_for("companies.user_list"))
    old_role = user.role
    user.role = new_role
    audit_service.log_action(
        action="user_role_changed", entity_type="user",
        entity_id=user.id, tenant_id=user.tenant_id,
        before_value={"role": old_role},
        after_value={"role": new_role},
    )
    db.session.commit()
    flash(f"Role for {user.display_name} changed to '{new_role}'.", "success")
    return redirect(url_for("companies.user_list"))


@companies_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
def toggle_user_active(user_id):
    """Deactivate or reactivate a user."""
    user = db.get_or_404(User, user_id)
    if user.is_super_admin:
        flash("Cannot deactivate a super admin.", "warning")
        return redirect(url_for("companies.user_list"))
    user.is_active_user = not user.is_active_user
    audit_service.log_action(
        action="user_activated" if user.is_active_user else "user_deactivated",
        entity_type="user", entity_id=user.id, tenant_id=user.tenant_id,
    )
    db.session.commit()
    state = "reactivated" if user.is_active_user else "deactivated"
    flash(f"User '{user.display_name}' {state}.", "info")
    return redirect(url_for("companies.user_list"))


@companies_bp.route("/users/<int:user_id>/delete", methods=["POST"])
def delete_user(user_id):
    """
    Hard-delete a user record permanently.

    Only for accounts created by mistake or test accounts with no trade data.
    Any user with trade history should be deactivated instead.
    """
    user = db.get_or_404(User, user_id)
    if user.is_super_admin:
        flash("Cannot delete a super admin account.", "warning")
        return redirect(url_for("companies.user_list"))
    if user.id == current_user.id:
        flash("Cannot delete your own account.", "warning")
        return redirect(url_for("companies.user_list"))
    name = user.display_name
    email = user.email
    tenant_id = user.tenant_id
    audit_service.log_action(
        action="user_deleted", entity_type="user",
        entity_id=user_id, tenant_id=tenant_id,
        before_value={"email": email, "display_name": name},
    )
    db.session.delete(user)
    db.session.commit()
    flash(f"User '{name}' permanently deleted.", "warning")
    return redirect(url_for("companies.user_list"))
