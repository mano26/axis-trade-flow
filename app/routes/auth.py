# -*- coding: utf-8 -*-
# =============================================================================
# Authentication Routes
# =============================================================================
# Handles login, logout, and session management.
#
# REGULATORY NOTE: All login attempts (successful and failed) should be
# logged for security audit purposes. Session duration is limited to one
# trading day (8 hours) per the config.
# =============================================================================

import traceback

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from datetime import datetime, timezone
from app.extensions import db, mail
from app.models.user import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/")
def root():
    """Redirect bare domain root to the orders page."""
    return redirect(url_for("orders.index"))


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
    """User guide - trade entry syntax, workflow, and reference."""
    return render_template("guide.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Log the user out and redirect to login page."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """
    Step 1 of the self-service password reset flow.

    GET:  Render a form asking for the user's email address.
    POST: If an account with that email exists, send a password-reset link.
          Always show the same success message to prevent user-enumeration.
    """
    if current_user.is_authenticated:
        return redirect(url_for("orders.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()

        if user and user.is_active:
            token = user.get_reset_token()
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            msg = Message(
                subject="Reset your AXIS Trade Flow password",
                recipients=[user.email],
            )
            msg.html = render_template(
                "email/password_reset.html",
                user=user,
                reset_url=reset_url,
            )
            try:
                mail.send(msg)
                current_app.logger.info("Password reset email sent to %s", user.email)
            except Exception as exc:
                current_app.logger.error(
                    "Failed to send password reset email to %s: %s\n%s",
                    user.email,
                    exc,
                    traceback.format_exc(),
                )

        # Always show the same message to prevent user-enumeration attacks
        flash(
            "If an account with that email exists, you'll receive a reset link shortly.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """
    Step 2 of the self-service password reset flow.

    GET:  Verify the token and render the new-password form.
    POST: Set the new password and redirect to login.
    """
    if current_user.is_authenticated:
        return redirect(url_for("orders.index"))

    user = User.verify_reset_token(token)
    if user is None:
        flash("That reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("auth/new_password.html", token=token)

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("auth/new_password.html", token=token)

        user.set_password(password)
        db.session.commit()
        # TODO: Log password change to audit trail
        flash("Your password has been updated. Please sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/new_password.html", token=token)