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
            is_first_login = user.last_login_at is None
            user.last_login_at = datetime.now(timezone.utc)
            db.session.commit()
            login_user(user, remember=False)
            # TODO: Log successful login to audit trail
            flash("Logged in successfully.", "success")
            next_page = request.args.get("next")
            if is_first_login:
                return redirect(url_for("auth.guide"))
            return redirect(next_page or url_for("orders.index"))

        # TODO: Log failed login attempt to audit trail
        flash("Invalid email or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/guide")
@login_required
def guide():
    """User guide — trade entry syntax, workflow, and reference."""
    return render_template("guide.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Log the user out and redirect to login page."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))