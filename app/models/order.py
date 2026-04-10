# =============================================================================
# Order Model
# =============================================================================
# Represents a trade order entered via the SOFR trade string syntax. An order
# consists of one or more legs (individual option or futures positions) parsed
# from the trade string.
#
# LIFECYCLE:
#   OPEN → CANCELLED
#   OPEN → MODIFIED (returns to OPEN with audit trail)
#   OPEN → PARTIAL_FILL → FILLED
#   OPEN → PARTIAL_FILL → PARTIAL_CANCELLED
#   OPEN → FILLED
#   FILLED/PARTIAL_FILL/PARTIAL_CANCELLED → REPORTED
#   REPORTED → REPORT_ACCEPTED / REPORT_FAILED
#   Any filled state → AMENDED (with audit trail)
#
# REGULATORY NOTE: Orders are never hard-deleted. The deleted_at column
# supports soft-delete for admin use only, preserving the full audit trail.
# All state transitions are logged in the audit_log table.
# =============================================================================

from datetime import datetime, timezone, date
from app.extensions import db
from app.models.tenant import TenantMixin


class OrderStatus:
    """
    Enumeration of order lifecycle states.

    Each status transition is validated by the Order.transition_to() method
    to enforce the state machine rules documented in ORDER_LIFECYCLE.md.
    """
    OPEN = "open"
    CANCELLED = "cancelled"
    PARTIAL_FILL = "partial_fill"
    PARTIAL_CANCELLED = "partial_cancelled"
    FILLED = "filled"
    AMENDED = "amended"
    REPORTED = "reported"
    REPORT_ACCEPTED = "report_accepted"
    REPORT_FAILED = "report_failed"

    # -------------------------------------------------------------------------
    # Valid state transitions
    # -------------------------------------------------------------------------
    # Maps each status to the set of statuses it can transition to.
    # The MODIFIED action does not change status — it stays OPEN — but is
    # tracked in the audit log.
    TRANSITIONS = {
        OPEN: {CANCELLED, PARTIAL_FILL, FILLED},
        PARTIAL_FILL: {PARTIAL_FILL, PARTIAL_CANCELLED, FILLED, REPORTED},
        PARTIAL_CANCELLED: {REPORTED, AMENDED},
        FILLED: {REPORTED, AMENDED},
        AMENDED: {REPORTED},
        REPORTED: {REPORT_ACCEPTED, REPORT_FAILED},
        REPORT_ACCEPTED: set(),     # Terminal state
        REPORT_FAILED: {REPORTED},  # Can retry submission
        CANCELLED: set(),           # Terminal state
    }


class Order(TenantMixin, db.Model):
    """
    A trade order entered via the SOFR trade string syntax.

    One order maps to one ticket number. The order contains the raw trade
    string, parsed metadata, and one or more OrderLeg records representing
    the individual option/futures positions.

    Partial fills accumulate as Fill records against this order. The
    remaining_quantity field tracks how much of the original order is still
    working.
    """
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)

    # --- Ticket Identification ---
    ticket_number = db.Column(
        db.Integer,
        nullable=False,
        doc="Sequential ticket number (1–9999) assigned at order entry. "
            "Unique per tenant per trading day."
    )
    ticket_display = db.Column(
        db.String(4),
        nullable=False,
        doc="Zero-padded ticket number for display (e.g., '0042')."
    )
    trade_date = db.Column(
        db.Date,
        nullable=False,
        default=lambda: date.today(),
        index=True,
        doc="The trading date this order was entered. Used with ticket_number "
            "for uniqueness and for the daily order log view."
    )

    # --- Trade String ---
    raw_input = db.Column(
        db.Text,
        nullable=False,
        doc="The original trade string as entered by the user, preserved "
            "exactly for audit purposes (e.g., 'SFRH6 C 96.00 96.25 CS 4/500')."
    )

    # --- Parsed Trade Metadata ---
    # These fields are populated by the trade parser and represent the
    # interpreted meaning of the trade string.
    direction = db.Column(
        db.String(1),
        nullable=False,
        doc="Overall direction: 'B' (buy/debit) or 'S' (sell/credit), "
            "derived from the price/qty format (slash = buy, at-sign = sell)."
    )
    total_quantity = db.Column(
        db.Integer,
        nullable=False,
        doc="Original order quantity parsed from the trade string."
    )
    filled_quantity = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        doc="Cumulative quantity filled across all Fill records. "
            "remaining_quantity = total_quantity - filled_quantity."
    )
    package_premium = db.Column(
        db.Float,
        nullable=True,
        doc="Package premium parsed from the trade string, stored as a "
            "decimal (e.g., 0.0400 for 4 ticks). Used for price reconciliation."
    )
    strategy = db.Column(
        db.String(20),
        nullable=True,
        doc="Primary strategy type (e.g., 'cs', 'bflyc', 'straddle', 'single'). "
            "Derived from the trade parser."
    )
    is_generic = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
        doc="If True, this order was entered in generic mode. Legs are "
            "manually entered by the user instead of parsed from the trade string."
    )

    # --- Account Info ---
    # Set from the UI when the order is entered or when counterparties are added.
    house = db.Column(
        db.String(50),
        nullable=True,
        doc="Clearing house identifier (e.g., 'GFI')."
    )
    account = db.Column(
        db.String(50),
        nullable=True,
        doc="Customer account identifier."
    )
    bk_broker = db.Column(
        db.String(50),
        nullable=True,
        doc="BK Broker for trades with futures legs attached. Optional but "
            "prompted on counterparty save if futures legs are present."
    )

    # --- Status ---
    status = db.Column(
        db.String(30),
        nullable=False,
        default=OrderStatus.OPEN,
        index=True,
        doc="Current lifecycle status. See OrderStatus for valid values "
            "and transition rules."
    )

    # --- User Tracking ---
    created_by_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        doc="The user who entered this order."
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
    time_in = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        doc="When the order was first taken (entered). Set automatically on "
            "order creation."
    )
    time_out = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        doc="When the order was completed (filled or cancelled). Set "
            "automatically on terminal status transitions."
    )
    modification_timestamps = db.Column(
        db.JSON,
        default=list,
        nullable=False,
        doc="List of ISO-format timestamps for each modification. "
            "Appended each time the order is modified."
    )
    deleted_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        doc="Soft-delete timestamp. Non-null means the record is logically "
            "deleted. Admin-only action with audit trail."
    )

    # --- Relationships ---
    tenant = db.relationship("Tenant", back_populates="orders")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    legs = db.relationship(
        "OrderLeg",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderLeg.leg_index",
    )
    fills = db.relationship(
        "Fill",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="Fill.created_at",
    )

    # --- Unique Constraint ---
    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id", "trade_date", "ticket_number",
            name="uq_tenant_date_ticket"
        ),
    )

    # -------------------------------------------------------------------------
    # State Machine
    # -------------------------------------------------------------------------

    @property
    def remaining_quantity(self) -> int:
        """Quantity still working (unfilled)."""
        return self.total_quantity - self.filled_quantity

    @property
    def has_futures_legs(self) -> bool:
        """True if any leg is a futures leg (no strike, no option type)."""
        return any(
            leg.option_type is None and leg.strike is None
            for leg in self.legs
        )

    def can_transition_to(self, new_status: str) -> bool:
        """
        Check whether the order can move to the given status.

        REGULATORY NOTE: Enforcing the state machine prevents invalid
        lifecycle transitions (e.g., cancelling an already-filled order).
        """
        valid_targets = OrderStatus.TRANSITIONS.get(self.status, set())
        return new_status in valid_targets

    def transition_to(self, new_status: str) -> None:
        """
        Move the order to a new status.
        Auto-sets time_out on terminal statuses.
        Partial cancelled: shrinks total_quantity to filled_quantity and
        transitions to FILLED.
        """
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Invalid transition: {self.status} → {new_status} "
                f"(allowed: {OrderStatus.TRANSITIONS.get(self.status, set())})"
            )

        # Partial cancel: shrink order to filled amount, become FILLED
        if new_status == OrderStatus.PARTIAL_CANCELLED:
            self.total_quantity = self.filled_quantity
            self.status = OrderStatus.FILLED
            self.time_out = datetime.now(timezone.utc)
            return

        self.status = new_status
        # Auto-set time_out on completion/cancellation
        terminal_statuses = {
            OrderStatus.CANCELLED, OrderStatus.FILLED,
        }
        if new_status in terminal_statuses and self.time_out is None:
            self.time_out = datetime.now(timezone.utc)

    def __repr__(self):
        return (
            f"<Order #{self.ticket_display} {self.status} "
            f"({self.filled_quantity}/{self.total_quantity})>"
        )


class OrderLeg(db.Model):
    """
    A single leg (option or futures position) within an order.

    Legs are created by the trade parser when the order is entered. Each leg
    represents one row in the confirmation grid (equivalent to one row in the
    VBA tool's Sheet 1 output).

    REGULATORY NOTE: Leg data is immutable after creation except via the
    AMENDED workflow, which requires audit trail documentation.
    """
    __tablename__ = "order_legs"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leg_index = db.Column(
        db.Integer,
        nullable=False,
        doc="Zero-based position of this leg within the order. Used for "
            "display ordering and to maintain correspondence with the "
            "original trade string."
    )

    # --- Leg Details ---
    # These fields correspond to the columns in the VBA tool's confirmation
    # grid: Side, Volume, Market, Contract, Expiry, Strike, C/P, Price.
    side = db.Column(
        db.String(1),
        nullable=False,
        doc="'B' (buy) or 'S' (sell) for this specific leg."
    )
    volume = db.Column(
        db.Integer,
        nullable=False,
        doc="Number of contracts for this leg at the order's original quantity."
    )
    market = db.Column(
        db.String(10),
        default="CME",
        nullable=False,
        doc="Exchange identifier. Always 'CME' for SOFR products."
    )
    contract_type = db.Column(
        db.String(10),
        nullable=False,
        doc="Contract type code: 'SR3' (quarterly SOFR), 'S0', 'S2', 'S3' "
            "(pack/short-dated contracts)."
    )
    expiry = db.Column(
        db.String(10),
        nullable=False,
        doc="Expiry in 'MMMYY' format (e.g., 'MAR26', 'JUN27')."
    )
    strike = db.Column(
        db.Float,
        nullable=True,
        doc="Strike price (e.g., 96.0000). Null for futures legs."
    )
    option_type = db.Column(
        db.String(1),
        nullable=True,
        doc="'C' (call) or 'P' (put). Null for futures legs."
    )
    price = db.Column(
        db.Float,
        nullable=True,
        doc="Fill price for this leg. Null until the order is filled and "
            "prices are entered. Subject to price reconciliation validation."
    )

    # --- Card/Ticket Rendering Metadata ---
    # These fields support the card and ticket HTML generators.
    mo_card_code = db.Column(
        db.String(10),
        nullable=True,
        doc="Contract month code for card display (e.g., 'SFRH6'). "
            "For futures legs, this is the quarterly future code."
    )
    package_premium = db.Column(
        db.Float,
        nullable=True,
        doc="Package premium stamped on this leg (hidden column in VBA). "
            "Used for price reconciliation grouping."
    )
    suppress_premium = db.Column(
        db.Boolean,
        default=False,
        nullable=False,
        doc="If True, this leg is part of a VS or bracket trade and its "
            "premium is displayed at the package level, not per-leg."
    )

    # --- Relationships ---
    order = db.relationship("Order", back_populates="legs")

    def __repr__(self):
        opt = self.option_type or "FUT"
        return f"<OrderLeg {self.side} {self.volume} {self.contract_type} {self.expiry} {self.strike} {opt}>"