# -*- coding: utf-8 -*-
# =============================================================================
# Print Event Model
# =============================================================================
# Records every card or ticket print action for regulatory audit trail.
#
# REGULATORY NOTE: Print events are immutable. When cards or tickets are
# reprinted after an amendment, a new PrintEvent is created — the old one
# is never modified. This provides a complete history of every document
# generated from the system.
# =============================================================================

from datetime import datetime, timezone
from app.extensions import db


class PrintEventType:
    """Enumeration of printable document types."""
    CARD = "card"
    TICKET = "ticket"


class PrintEvent(db.Model):
    """
    An immutable record of a card or ticket print action.

    Each print event captures a snapshot of the data at the time of printing,
    so that even if the underlying order is later amended, the exact content
    of the previously printed document can be reconstructed.
    """
    __tablename__ = "print_events"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(
        db.String(20),
        nullable=False,
        doc="'card' or 'ticket'."
    )
    printed_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        doc="The user who initiated the print action."
    )

    # --- Data Snapshot ---
    # JSON blob capturing the exact data used to render the document.
    # This allows reconstruction of the printed output even after amendments.
    data_snapshot = db.Column(
        db.JSON,
        nullable=False,
        doc="JSON snapshot of all data used to render the card/ticket. "
            "Includes legs, prices, counterparties, and formatting metadata."
    )

    # --- Timestamps ---
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- Relationships ---
    order = db.relationship("Order")
    printed_by = db.relationship("User", foreign_keys=[printed_by_id])

    def __repr__(self):
        return f"<PrintEvent {self.event_type} order={self.order_id}>"