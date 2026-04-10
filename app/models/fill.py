# =============================================================================
# Fill Model
# =============================================================================
# Represents a fill event against an order. An order can have multiple fills
# (partial fills accumulate). Each fill has a quantity and, once counterparties
# are entered, one or more FillCounterparty records.
#
# KEY BUSINESS RULE: Fills are always proportional across all legs of the
# order. A fill quantity of 250 on a 500-lot butterfly means each leg filled
# at 50% of its original volume. Individual legs do not fill independently.
#
# REGULATORY NOTE: Fill records are immutable after creation except via the
# AMENDED workflow. The allocation_status field tracks whether counterparties
# have been entered — card/ticket generation is blocked until allocation is
# complete.
# =============================================================================

from datetime import datetime, timezone
from app.extensions import db
from app.models.tenant import TenantMixin


class AllocationStatus:
    """
    Counterparty allocation status for a fill.

    PENDING_ALLOCATION — Fill is recorded but counterparties have not yet
                         been entered. This occurs when a broker needs time
                         to identify the other side of the trade.
    ALLOCATED          — All counterparties have been entered and quantities
                         reconcile to the fill quantity.
    """
    PENDING = "pending_allocation"
    ALLOCATED = "allocated"


class Fill(TenantMixin, db.Model):
    """
    A fill event against an order.

    Each fill represents a quantity of the order that has been executed.
    Multiple fills can accumulate against a single order (partial fills).
    The sum of all fill quantities must not exceed the order's total_quantity.

    Prices are entered per-leg at the fill level and validated against the
    package premium via price reconciliation.
    """
    __tablename__ = "fills"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Fill Details ---
    fill_quantity = db.Column(
        db.Integer,
        nullable=False,
        doc="Number of contracts filled in this event. Must be positive and "
            "must not cause total filled to exceed order total_quantity."
    )
    fill_timestamp = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        doc="When this fill occurred. May differ from created_at if the fill "
            "is entered retroactively."
    )

    # --- Allocation Status ---
    allocation_status = db.Column(
        db.String(30),
        nullable=False,
        default=AllocationStatus.PENDING,
        doc="Whether counterparties have been fully entered for this fill. "
            "Card/ticket generation requires ALLOCATED status on all fills."
    )

    # --- User Tracking ---
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        doc="The user who recorded this fill."
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
    order = db.relationship("Order", back_populates="fills")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    counterparties = db.relationship(
        "FillCounterparty",
        back_populates="fill",
        cascade="all, delete-orphan",
        order_by="FillCounterparty.id",
    )
    leg_prices = db.relationship(
        "FillLegPrice",
        back_populates="fill",
        cascade="all, delete-orphan",
        order_by="FillLegPrice.leg_index",
    )

    def __repr__(self):
        return (
            f"<Fill order={self.order_id} qty={self.fill_quantity} "
            f"status={self.allocation_status}>"
        )


class FillLegPrice(db.Model):
    """
    Per-leg fill price for a specific fill event.

    When a fill is recorded, the user enters the price for each leg of the
    order. These prices are validated via price reconciliation: the net of
    all leg prices (accounting for buy/sell sides and volume ratios) must
    equal the package premium within floating-point tolerance.

    REGULATORY NOTE: Price changes after initial entry are tracked in the
    audit log. The original and amended prices are both preserved.
    """
    __tablename__ = "fill_leg_prices"

    id = db.Column(db.Integer, primary_key=True)
    fill_id = db.Column(
        db.Integer,
        db.ForeignKey("fills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leg_index = db.Column(
        db.Integer,
        nullable=False,
        doc="Corresponds to OrderLeg.leg_index to identify which leg this "
            "price applies to."
    )
    price = db.Column(
        db.Float,
        nullable=False,
        doc="Fill price for this leg (e.g., 0.0275 for 2.75 ticks)."
    )

    # --- Relationships ---
    fill = db.relationship("Fill", back_populates="leg_prices")

    __table_args__ = (
        db.UniqueConstraint("fill_id", "leg_index", name="uq_fill_leg_price"),
    )

    def __repr__(self):
        return f"<FillLegPrice fill={self.fill_id} leg={self.leg_index} price={self.price}>"


class FillCounterparty(db.Model):
    """
    A counterparty allocation for a fill.

    Each fill can have multiple counterparties (the fill quantity is split
    across them). The sum of all counterparty quantities for a fill must
    equal the fill_quantity.

    These records directly correspond to the counterparty rows on Sheet 2
    of the VBA tool and drive the card generation grouping logic (by
    bracket + broker).

    REGULATORY NOTE: Counterparty data is sensitive — it identifies the
    firms on each side of a trade. Access is restricted to the owning
    tenant's users.
    """
    __tablename__ = "fill_counterparties"

    id = db.Column(db.Integer, primary_key=True)
    fill_id = db.Column(
        db.Integer,
        db.ForeignKey("fills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Counterparty Details ---
    # These fields correspond to the Sheet 2 columns in the VBA tool:
    # QTY, BROKER, OPPOSITE/HOUSE, BRACKET, NOTES
    quantity = db.Column(
        db.Integer,
        nullable=False,
        doc="Number of contracts allocated to this counterparty."
    )
    broker = db.Column(
        db.String(50),
        nullable=False,
        doc="Broker identifier (e.g., 'GFI', 'ICAP', 'BGC')."
    )
    symbol = db.Column(
        db.String(100),
        nullable=False,
        doc="Counterparty firm symbol or identifier. May contain a slash "
            "separator for display formatting (e.g., 'CITADEL/CIT')."
    )
    bracket = db.Column(
        db.String(10),
        nullable=False,
        doc="Trading bracket code (single letter A–Z, $, %, or digit). "
            "Used to group cards and highlighted on the ticket bracket row."
    )
    notes = db.Column(
        db.Text,
        nullable=True,
        doc="Optional free-text notes for this counterparty allocation."
    )

    # --- Timestamps ---
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- Relationships ---
    fill = db.relationship("Fill", back_populates="counterparties")

    def __repr__(self):
        return (
            f"<FillCounterparty fill={self.fill_id} "
            f"{self.quantity}x {self.symbol} [{self.bracket}]>"
        )
