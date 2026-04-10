# =============================================================================
# Model Registry
# =============================================================================
# This module imports all SQLAlchemy models so that Alembic's migration
# autogenerate can discover them. Any new model must be imported here.
#
# Usage:
#   from app.models import Order, OrderLeg, Fill, ...
# =============================================================================

from app.models.tenant import Tenant, TenantMixin          # noqa: F401
from app.models.user import User, UserRole                  # noqa: F401
from app.models.order import Order, OrderLeg, OrderStatus   # noqa: F401
from app.models.fill import (                               # noqa: F401
    Fill,
    FillLegPrice,
    FillCounterparty,
    AllocationStatus,
)
from app.models.print_event import PrintEvent, PrintEventType  # noqa: F401
from app.models.audit import AuditLog, AuditAction             # noqa: F401
from app.models.lookup import LookupValue, LookupType          # noqa: F401
