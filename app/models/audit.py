# -*- coding: utf-8 -*-
# =============================================================================
# Audit Log Model
# =============================================================================
# Immutable, append-only log of every significant action in the system.
# Every state transition, data edit, print event, and admin action is
# recorded here with before/after values.
#
# REGULATORY NOTE: This table is the primary compliance artifact. It must
# never be modified or truncated. In production, consider replicating to
# a separate audit database or write-once storage (e.g., S3 with object
# lock) for tamper-evidence.
#
# The audit log is designed to answer questions like:
#   - Who changed this order's status and when?
#   - What were the original prices before amendment?
#   - Who printed this ticket and what data was on it?
#   - When was this counterparty added or modified?
# =============================================================================

from datetime import datetime, timezone
from app.extensions import db


class AuditAction:
    """
    Enumeration of auditable actions.

    Each action corresponds to a specific type of system event that must
    be logged for regulatory compliance.
    """
    # --- Order Lifecycle ---
    ORDER_CREATED = "order_created"
    ORDER_MODIFIED = "order_modified"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_STATUS_CHANGE = "order_status_change"

    # --- Fill Events ---
    FILL_CREATED = "fill_created"
    FILL_PRICE_ENTERED = "fill_price_entered"
    FILL_PRICE_AMENDED = "fill_price_amended"

    # --- Counterparty Events ---
    COUNTERPARTY_ADDED = "counterparty_added"
    COUNTERPARTY_MODIFIED = "counterparty_modified"
    COUNTERPARTY_REMOVED = "counterparty_removed"
    ALLOCATION_COMPLETED = "allocation_completed"

    # --- Print Events ---
    CARDS_PRINTED = "cards_printed"
    TICKET_PRINTED = "ticket_printed"

    # --- Exchange Reporting ---
    EXCHANGE_SUBMITTED = "exchange_submitted"
    EXCHANGE_ACCEPTED = "exchange_accepted"
    EXCHANGE_REJECTED = "exchange_rejected"

    # --- Admin Actions ---
    USER_CREATED = "user_created"
    USER_DEACTIVATED = "user_deactivated"
    RECORD_DELETED = "record_deleted"


class AuditLog(db.Model):
    """
    Immutable audit trail entry.

    Every row captures who did what, when, to which record, and what
    the before/after state looked like. The before_value and after_value
    columns store JSON blobs for maximum flexibility — they can capture
    anything from a single field change to a full object snapshot.

    This table is append-only. There is no update or delete operation
    defined on this model.
    """
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # --- What happened ---
    action = db.Column(
        db.String(50),
        nullable=False,
        index=True,
        doc="The type of action performed. See AuditAction for values."
    )

    # --- Who did it ---
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        doc="The user who performed the action."
    )

    # --- What was affected ---
    # Generic foreign key pattern: entity_type + entity_id allows the audit
    # log to reference any model without creating FK constraints to every table.
    entity_type = db.Column(
        db.String(50),
        nullable=False,
        doc="The type of entity affected (e.g., 'order', 'fill', 'counterparty')."
    )
    entity_id = db.Column(
        db.Integer,
        nullable=False,
        doc="The primary key of the affected entity."
    )

    # --- Before/After State ---
    before_value = db.Column(
        db.JSON,
        nullable=True,
        doc="JSON snapshot of the entity state before the change. "
            "Null for creation events."
    )
    after_value = db.Column(
        db.JSON,
        nullable=True,
        doc="JSON snapshot of the entity state after the change. "
            "Null for deletion events."
    )

    # --- Context ---
    notes = db.Column(
        db.Text,
        nullable=True,
        doc="Optional human-readable description of the change."
    )
    ip_address = db.Column(
        db.String(45),
        nullable=True,
        doc="IP address of the user at the time of the action. "
            "Supports both IPv4 and IPv6."
    )

    # --- Timestamp ---
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    # --- Relationships (read-only) ---
    user = db.relationship("User", foreign_keys=[user_id])

    # --- Indexes for common query patterns ---
    __table_args__ = (
        db.Index("ix_audit_entity", "entity_type", "entity_id"),
        db.Index("ix_audit_tenant_date", "tenant_id", "created_at"),
    )

    def __repr__(self):
        return (
            f"<AuditLog {self.action} {self.entity_type}:{self.entity_id} "
            f"by user {self.user_id}>"
        )