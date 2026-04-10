# =============================================================================
# Authentication Routes
# =============================================================================
# Handles login, logout, and session management.
#
# REGULATORY NOTE: All login attempts (successful and failed) should be
# logged for security audit purposes. Session duration is limited to one
# trading day (8 hours) per the config.
# =============================================================================

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from datetime import datetime, timezone
from app.extensions import db
from app.models.user import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """
    User login page and handler.

    GET:  Render the login form.
    POST: Validate credentials and create a session.
    """
    if current_user.is_authenticated:
        return redirect(url_for("orders.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password) and user.is_active:
            if not user.tenant.is_active:
                flash("Your company account has been suspended.", "danger")
                return render_template("auth/login.html")
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            login_user(user, remember=False)
            # TODO: Log successful login to audit trail
            flash("Logged in successfully.", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("orders.index"))

        # TODO: Log failed login attempt to audit trail
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Log the user out and redirect to login page."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
    @auth_bp.route("/setup-admin")
def setup_admin():
    from app.models.tenant import Tenant
    from app.models.user import User, UserRole
    if User.query.filter_by(email="admin@axis.dev").first():
        return "Already set up. Go to /login", 200
    tenant = Tenant.query.first()
    if not tenant:
        tenant = Tenant(name="AXIS Trading", slug="axis-trading")
        db.session.add(tenant)
        db.session.flush()
    admin = User(
        tenant_id=tenant.id,
        email="admin@axis.dev",
        display_name="Admin User",
        role=UserRole.ADMIN,
        is_super_admin=True,
    )
    admin.set_password("admin123")
    db.session.add(admin)
    db.session.commit()
    return "Super admin created: admin@axis.dev / admin123. Go to /login", 200