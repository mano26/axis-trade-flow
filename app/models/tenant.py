# -*- coding: utf-8 -*-
# =============================================================================
# Tenant Model
# =============================================================================
# Represents a firm or organization using the platform. Every row in every
# business table includes a tenant_id foreign key for row-level isolation.
#
# REGULATORY NOTE: Tenant isolation is critical for data confidentiality.
# Firm A must never see Firm B's trades, orders, or counterparty data.
# All queries must be scoped by tenant_id. The TenantMixin base class
# (defined below) enforces this at the model level.
# =============================================================================

from datetime import datetime, timezone
from app.extensions import db


class Tenant(db.Model):
    """
    A firm or organization that uses the AXIS Trade Flow platform.

    Each tenant has its own isolated set of users, orders, fills, and
    counterparty data. Tenant-scoped ticket numbering resets daily.
    """
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(
        db.String(200),
        nullable=False,
        doc="Display name of the firm (e.g., 'Acme Trading LLC')."
    )
    slug = db.Column(
        db.String(100),
        unique=True,
        nullable=False,
        doc="URL-safe identifier for the tenant (e.g., 'acme-trading')."
    )
    brand_name = db.Column(
        db.String(200),
        nullable=True,
        doc="Custom branding name shown in the nav bar (e.g., 'Bullseye Trade Flow'). "
            "Falls back to 'AXIS TRADE FLOW' if not set."
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
        doc="Whether this tenant's account is active. Inactive tenants "
            "cannot log in or create orders."
    )

    # --- Ticket Counter State ---
    # Persistent sequential counter across all days. Increments 1 per order,
    # wraps from 9999 back to 1. Never resets daily — each tenant has a
    # continuous sequence so ticket numbers are unique within a tenant's lifetime
    # (until the wrap, after which the ticket_date on the Order distinguishes them).
    current_ticket_number = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        doc="Last ticket number assigned. Increments each order, wraps 9999→1. Never resets daily."
    )
    ticket_date = db.Column(
        db.Date,
        nullable=True,
        doc="Retained for schema compatibility. No longer used for counter reset logic."
    )

    # --- Timestamps ---
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- Relationships ---
    users = db.relationship("User", back_populates="tenant", lazy="dynamic")
    orders = db.relationship("Order", back_populates="tenant", lazy="dynamic")

    def __repr__(self):
        return f"<Tenant {self.slug}>"


class TenantMixin:
    """
    Mixin that adds a tenant_id foreign key to any model.

    All business-data models (Order, Fill, etc.) should inherit from this
    mixin to enforce row-level tenant isolation. Queries should always
    filter by tenant_id.

    REGULATORY NOTE: Omitting the tenant_id filter from any query is a
    data isolation violation. Code reviews should verify tenant scoping
    on every database access.
    """

    @classmethod
    def _tenant_id_column(cls):
        """Define the tenant_id column. Called via __init_subclass__."""
        return db.Column(
            db.Integer,
            db.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
            doc="Foreign key to the owning tenant. Required on every row."
        )

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "tenant_id"):
            cls.tenant_id = cls._tenant_id_column()