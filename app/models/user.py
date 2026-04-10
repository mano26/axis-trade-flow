# =============================================================================
# User Model
# =============================================================================
# Represents an individual user within a tenant. Integrates with Flask-Login
# for session management and includes role-based access control.
#
# REGULATORY NOTE: User accounts are never hard-deleted. Deactivated users
# retain their audit trail. Password hashes use Werkzeug's PBKDF2-SHA256
# with a unique salt per user.
# =============================================================================

from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class UserRole:
    """
    Enumeration of user roles.

    ADMIN  — Full access including user management, tenant settings, and
             the ability to delete records. Typically one per firm.
    USER   — Operational access: enter orders, enter counterparties, fill,
             modify, print, send to exchange. Cannot delete records or
             manage users.

    Future roles (stubbed for schema readiness):
    AUDITOR — Read-only access to all data and audit trails.
    """
    ADMIN = "admin"
    USER = "user"
    AUDITOR = "auditor"  # Reserved for future use

    ALL = [ADMIN, USER, AUDITOR]


class User(UserMixin, db.Model):
    """
    An individual user account within a tenant.

    Inherits from Flask-Login's UserMixin to provide is_authenticated,
    is_active, is_anonymous, and get_id() for session management.
    """
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        doc="The tenant (firm) this user belongs to."
    )
    email = db.Column(
        db.String(255),
        unique=True,
        nullable=False,
        index=True,
        doc="Email address used for login. Must be unique across all tenants."
    )
    password_hash = db.Column(
        db.String(256),
        nullable=False,
        doc="PBKDF2-SHA256 hash of the user's password. Never stored in "
            "plaintext."
    )
    display_name = db.Column(
        db.String(200),
        nullable=False,
        doc="Full name displayed in the UI and on audit trail entries."
    )
    role = db.Column(
        db.String(20),
        nullable=False,
        default=UserRole.USER,
        doc="Access level: 'admin', 'user', or 'auditor'."
    )
    is_active_user = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
        doc="Whether this user can log in. Deactivated users are locked out "
            "but their data and audit history are preserved."
    )

    # --- Timestamps ---
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_login_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        doc="Timestamp of the most recent successful login."
    )

    # --- Relationships ---
    tenant = db.relationship("Tenant", back_populates="users")

    # -------------------------------------------------------------------------
    # Password Management
    # -------------------------------------------------------------------------

    def set_password(self, password: str) -> None:
        """
        Hash and store the user's password.

        Uses Werkzeug's PBKDF2-SHA256 with a random salt. The plaintext
        password is never stored or logged.
        """
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """
        Verify a plaintext password against the stored hash.

        Returns True if the password matches, False otherwise.
        """
        return check_password_hash(self.password_hash, password)

    # -------------------------------------------------------------------------
    # Flask-Login Integration
    # -------------------------------------------------------------------------

    @property
    def is_active(self):
        """
        Flask-Login calls this to check if the user is allowed to log in.
        We delegate to is_active_user to avoid shadowing the UserMixin
        property name.
        """
        return self.is_active_user

    # -------------------------------------------------------------------------
    # Role Checks
    # -------------------------------------------------------------------------

    def is_admin(self) -> bool:
        """Returns True if the user has admin privileges."""
        return self.role == UserRole.ADMIN

    def can_delete(self) -> bool:
        """
        Returns True if the user is allowed to delete records.

        REGULATORY NOTE: Only admins can delete. All deletes are soft-deletes
        with audit trail entries.
        """
        return self.role == UserRole.ADMIN

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"
