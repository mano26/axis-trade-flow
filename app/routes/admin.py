# =============================================================================
# Admin Routes
# =============================================================================
# User management, lookup table management, and admin-only actions.
# URL prefix: /admin
# =============================================================================

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models.user import User, UserRole
from app.models.lookup import LookupValue, LookupType
from app.services import audit_service

admin_bp = Blueprint("admin", __name__)


@admin_bp.before_request
@login_required
def require_admin():
    if not current_user.is_admin():
        flash("Admin access required.", "danger")
        return redirect(url_for("orders.index"))


# =========================================================================
# User Management
# =========================================================================

@admin_bp.route("/users")
def user_list():
    users = User.query.filter_by(tenant_id=current_user.tenant_id).all()
    return render_template("auth/user_list.html", users=users)


@admin_bp.route("/users/create", methods=["GET", "POST"])
def create_user():
    if request.method == "GET":
        return render_template("auth/create_user.html", roles=UserRole.ALL)
    email = request.form.get("email", "").strip()
    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", UserRole.USER)
    if not all([email, display_name, password]):
        flash("All fields are required.", "warning")
        return redirect(url_for("admin.create_user"))
    if User.query.filter_by(email=email).first():
        flash("A user with that email already exists.", "warning")
        return redirect(url_for("admin.create_user"))
    user = User(
        tenant_id=current_user.tenant_id,
        email=email,
        display_name=display_name,
        role=role if role in UserRole.ALL else UserRole.USER,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    audit_service.log_action(
        action="user_created", entity_type="user",
        entity_id=user.id, tenant_id=current_user.tenant_id,
        after_value={"email": email, "role": role, "display_name": display_name},
    )
    db.session.commit()
    flash(f"User '{display_name}' created.", "success")
    return redirect(url_for("admin.user_list"))


@admin_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
def deactivate_user(user_id):
    user = User.query.filter_by(
        id=user_id, tenant_id=current_user.tenant_id,
    ).first_or_404()
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.user_list"))
    user.is_active_user = False
    audit_service.log_action(
        action="user_deactivated", entity_type="user",
        entity_id=user.id, tenant_id=current_user.tenant_id,
        before_value={"is_active": True}, after_value={"is_active": False},
    )
    db.session.commit()
    flash(f"User '{user.display_name}' deactivated.", "info")
    return redirect(url_for("admin.user_list"))


@admin_bp.route("/users/<int:user_id>/reactivate", methods=["POST"])
def reactivate_user(user_id):
    user = User.query.filter_by(
        id=user_id, tenant_id=current_user.tenant_id,
    ).first_or_404()
    user.is_active_user = True
    audit_service.log_action(
        action="user_reactivated", entity_type="user",
        entity_id=user.id, tenant_id=current_user.tenant_id,
        before_value={"is_active": False}, after_value={"is_active": True},
    )
    db.session.commit()
    flash(f"User '{user.display_name}' reactivated.", "success")
    return redirect(url_for("admin.user_list"))


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["GET", "POST"])
def reset_password(user_id):
    user = User.query.filter_by(
        id=user_id, tenant_id=current_user.tenant_id,
    ).first_or_404()
    if request.method == "GET":
        return render_template("auth/reset_password.html", user=user)
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    if not new_password or len(new_password) < 6:
        flash("Password must be at least 6 characters.", "warning")
        return redirect(url_for("admin.reset_password", user_id=user.id))
    if new_password != confirm_password:
        flash("Passwords do not match.", "warning")
        return redirect(url_for("admin.reset_password", user_id=user.id))
    user.set_password(new_password)
    audit_service.log_action(
        action="password_reset", entity_type="user",
        entity_id=user.id, tenant_id=current_user.tenant_id,
    )
    db.session.commit()
    flash(f"Password reset for '{user.display_name}'.", "success")
    return redirect(url_for("admin.user_list"))


# =========================================================================
# Lookup Table Management
# =========================================================================

@admin_bp.route("/lookups")
def lookup_list():
    """Show all lookup tables with their values."""
    lookups = {}
    for lt in LookupType.ALL:
        lookups[lt] = (
            LookupValue.query
            .filter_by(tenant_id=current_user.tenant_id, lookup_type=lt)
            .order_by(LookupValue.sort_order, LookupValue.value)
            .all()
        )
    return render_template(
        "admin/lookups.html",
        lookups=lookups,
        lookup_types=LookupType.ALL,
        lookup_labels=LookupType.LABELS,
    )


@admin_bp.route("/lookups/add", methods=["POST"])
def lookup_add():
    """Add one or more values to a lookup table. Supports comma-separated input."""
    lookup_type = request.form.get("lookup_type", "").strip()
    raw_value = request.form.get("value", "").strip().upper()

    if not lookup_type or lookup_type not in LookupType.ALL:
        flash("Invalid lookup type.", "danger")
        return redirect(url_for("admin.lookup_list"))
    if not raw_value:
        flash("Value cannot be empty.", "warning")
        return redirect(url_for("admin.lookup_list"))

    # Split on commas for bulk entry
    values = [v.strip() for v in raw_value.split(",") if v.strip()]
    added = []
    reactivated = []
    skipped = []

    max_sort = db.session.query(db.func.max(LookupValue.sort_order)).filter_by(
        tenant_id=current_user.tenant_id, lookup_type=lookup_type,
    ).scalar() or 0

    for value in values:
        existing = LookupValue.query.filter_by(
            tenant_id=current_user.tenant_id,
            lookup_type=lookup_type,
            value=value,
        ).first()
        if existing:
            if not existing.is_active:
                existing.is_active = True
                reactivated.append(value)
            else:
                skipped.append(value)
            continue

        max_sort += 1
        lv = LookupValue(
            tenant_id=current_user.tenant_id,
            lookup_type=lookup_type,
            value=value,
            sort_order=max_sort,
        )
        db.session.add(lv)
        added.append(value)

    db.session.commit()

    label = LookupType.LABELS.get(lookup_type, lookup_type)
    msgs = []
    if added:
        msgs.append(f"Added to {label}: {', '.join(added)}")
    if reactivated:
        msgs.append(f"Reactivated: {', '.join(reactivated)}")
    if skipped:
        msgs.append(f"Already exist: {', '.join(skipped)}")
    flash(". ".join(msgs) if msgs else "No changes.", "success" if added or reactivated else "info")
    return redirect(url_for("admin.lookup_list"))


@admin_bp.route("/lookups/<int:lookup_id>/deactivate", methods=["POST"])
def lookup_deactivate(lookup_id):
    """Deactivate a lookup value (hide from dropdowns, keep for history)."""
    lv = LookupValue.query.filter_by(
        id=lookup_id, tenant_id=current_user.tenant_id,
    ).first_or_404()
    lv.is_active = False
    db.session.commit()
    flash(f"'{lv.value}' deactivated.", "info")
    return redirect(url_for("admin.lookup_list"))


@admin_bp.route("/lookups/<int:lookup_id>/activate", methods=["POST"])
def lookup_activate(lookup_id):
    """Reactivate a previously deactivated lookup value."""
    lv = LookupValue.query.filter_by(
        id=lookup_id, tenant_id=current_user.tenant_id,
    ).first_or_404()
    lv.is_active = True
    db.session.commit()
    flash(f"'{lv.value}' reactivated.", "success")
    return redirect(url_for("admin.lookup_list"))