from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models.user import User
import logging

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("orders.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        logger.warning(f"Login attempt for: {email}")

        user = User.query.filter_by(email=email).first()
        logger.warning(f"User found: {user is not None}")

        if user:
            pw_ok = user.check_password(password)
            active = user.is_active
            logger.warning(f"Password OK: {pw_ok}, Active: {active}")

            if pw_ok and active:
                login_user(user, remember=False)
                flash("Logged in successfully.", "success")
                next_page = request.args.get("next")
                return redirect(next_page or url_for("orders.index"))

        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/setup-admin")
def setup_admin():
    from app.models.tenant import Tenant
    from app.models.user import User, UserRole
    if Tenant.query.first():
        return "Already set up. Go to /login", 200
    tenant = Tenant(name="AXIS Trading", slug="axis-trading")
    db.session.add(tenant)
    db.session.flush()
    admin = User(
        tenant_id=tenant.id,
        email="admin@axis.dev",
        display_name="Admin User",
        role=UserRole.ADMIN,
    )
    admin.set_password("admin123")
    db.session.add(admin)
    db.session.commit()
    return "Admin created: admin@axis.dev / admin123. Go to /login", 200