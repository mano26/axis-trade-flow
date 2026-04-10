# =============================================================================
# Lookup Models
# =============================================================================
# Tenant-scoped lookup tables for dropdown values used in trade entry.
# Admins manage these lists; users select from them when entering
# counterparties, brokers, accounts, etc.
#
# REGULATORY NOTE: Lookup values are soft-deleted (is_active flag) to
# preserve referential integrity with historical trade data.
# =============================================================================

from datetime import datetime, timezone
from app.extensions import db


class LookupType:
    """Enumeration of lookup table types."""
    FILLING_BROKER = "filling_broker"
    COUNTERPARTY = "counterparty"
    HOUSE = "house"
    ACCOUNT = "account"
    BRACKET = "bracket"

    ALL = [FILLING_BROKER, COUNTERPARTY, HOUSE, ACCOUNT, BRACKET]

    LABELS = {
        FILLING_BROKER: "Filling Broker",
        COUNTERPARTY: "Counterparty / House",
        HOUSE: "House",
        ACCOUNT: "Account",
        BRACKET: "Bracket",
    }


class LookupValue(db.Model):
    """
    A single value in a tenant-scoped lookup table.

    Each row represents one selectable option in a dropdown (e.g., one
    broker name, one counterparty symbol, one bracket code). Values are
    scoped to a tenant so each firm has its own lists.
    """
    __tablename__ = "lookup_values"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer,
        db.ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    lookup_type = db.Column(
        db.String(30),
        nullable=False,
        index=True,
        doc="The type of lookup: 'filling_broker', 'counterparty', "
            "'house', 'account', or 'bracket'.",
    )
    value = db.Column(
        db.String(200),
        nullable=False,
        doc="The display value (e.g., 'GFI', 'CITADEL/CIT', 'A').",
    )
    sort_order = db.Column(
        db.Integer,
        default=0,
        nullable=False,
        doc="Display order in the dropdown. Lower numbers appear first.",
    )
    is_active = db.Column(
        db.Boolean,
        default=True,
        nullable=False,
        doc="Inactive values are hidden from dropdowns but preserved "
            "for historical data integrity.",
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "tenant_id", "lookup_type", "value",
            name="uq_lookup_tenant_type_value",
        ),
    )

    def __repr__(self):
        return f"<LookupValue {self.lookup_type}={self.value}>"


def get_lookup_values(tenant_id: int, lookup_type: str) -> list[LookupValue]:
    """
    Get all active lookup values for a tenant and type, sorted by sort_order.
    """
    return (
        LookupValue.query
        .filter_by(tenant_id=tenant_id, lookup_type=lookup_type, is_active=True)
        .order_by(LookupValue.sort_order, LookupValue.value)
        .all()
    )
