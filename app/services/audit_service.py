# =============================================================================
# Audit Service
# =============================================================================
# Provides a clean interface for writing audit log entries. All state changes,
# data modifications, and significant actions flow through this service.
#
# REGULATORY NOTE: Every function in this module creates an immutable record
# in the audit_log table. These records must never be modified or deleted.
# The service captures the user, timestamp, entity, action, and before/after
# state for every auditable event.
# =============================================================================

from __future__ import annotations
from typing import Any, Optional
from flask import request
from flask_login import current_user
from app.extensions import db
from app.models.audit import AuditLog, AuditAction


def log_action(
    action: str,
    entity_type: str,
    entity_id: int,
    tenant_id: int,
    before_value: Optional[dict] = None,
    after_value: Optional[dict] = None,
    notes: Optional[str] = None,
    user_id: Optional[int] = None,
) -> AuditLog:
    """
    Write a single audit log entry.

    Parameters
    ----------
    action : str
        The action being performed (see AuditAction constants).
    entity_type : str
        The type of entity affected (e.g., 'order', 'fill').
    entity_id : int
        The primary key of the affected entity.
    tenant_id : int
        The tenant that owns the entity.
    before_value : dict, optional
        JSON-serializable snapshot of the entity before the change.
    after_value : dict, optional
        JSON-serializable snapshot of the entity after the change.
    notes : str, optional
        Human-readable description of the change.
    user_id : int, optional
        The user performing the action. Defaults to current_user.id.

    Returns
    -------
    AuditLog
        The created audit log entry (already added to the session).
    """
    # Determine user — use provided user_id, fall back to current_user
    if user_id is None:
        user_id = current_user.id if current_user and current_user.is_authenticated else None

    # Capture IP address from the request context
    ip_addr = None
    try:
        ip_addr = request.remote_addr
    except RuntimeError:
        pass  # Outside request context (e.g., in a script)

    entry = AuditLog(
        tenant_id=tenant_id,
        action=action,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        before_value=before_value,
        after_value=after_value,
        notes=notes,
        ip_address=ip_addr,
    )
    db.session.add(entry)
    return entry


# =========================================================================
# Convenience Functions
# =========================================================================
# These wrap log_action() with the correct action constant and entity type
# for the most common audit scenarios.
# =========================================================================

def log_order_created(order, tenant_id: int) -> AuditLog:
    """Log the creation of a new order."""
    return log_action(
        action=AuditAction.ORDER_CREATED,
        entity_type="order",
        entity_id=order.id,
        tenant_id=tenant_id,
        after_value={
            "ticket_display": order.ticket_display,
            "raw_input": order.raw_input,
            "direction": order.direction,
            "total_quantity": order.total_quantity,
            "strategy": order.strategy,
        },
    )


def log_order_status_change(
    order,
    tenant_id: int,
    old_status: str,
    new_status: str,
    notes: str = "",
) -> AuditLog:
    """Log an order status transition."""
    return log_action(
        action=AuditAction.ORDER_STATUS_CHANGE,
        entity_type="order",
        entity_id=order.id,
        tenant_id=tenant_id,
        before_value={"status": old_status},
        after_value={"status": new_status},
        notes=notes,
    )


def log_order_modified(
    order,
    tenant_id: int,
    before: dict,
    after: dict,
) -> AuditLog:
    """Log a modification to an unfilled order."""
    return log_action(
        action=AuditAction.ORDER_MODIFIED,
        entity_type="order",
        entity_id=order.id,
        tenant_id=tenant_id,
        before_value=before,
        after_value=after,
        notes="Order modified (same ticket, unfilled).",
    )


def log_fill_created(fill, tenant_id: int) -> AuditLog:
    """Log the creation of a new fill."""
    return log_action(
        action=AuditAction.FILL_CREATED,
        entity_type="fill",
        entity_id=fill.id,
        tenant_id=tenant_id,
        after_value={
            "order_id": fill.order_id,
            "fill_quantity": fill.fill_quantity,
        },
    )


def log_fill_price_amended(
    fill,
    tenant_id: int,
    before_prices: dict,
    after_prices: dict,
) -> AuditLog:
    """Log an amendment to fill leg prices."""
    return log_action(
        action=AuditAction.FILL_PRICE_AMENDED,
        entity_type="fill",
        entity_id=fill.id,
        tenant_id=tenant_id,
        before_value=before_prices,
        after_value=after_prices,
        notes="Fill prices amended.",
    )


def log_print_event(order, tenant_id: int, event_type: str) -> AuditLog:
    """Log a card or ticket print action."""
    action = (
        AuditAction.CARDS_PRINTED
        if event_type == "card"
        else AuditAction.TICKET_PRINTED
    )
    return log_action(
        action=action,
        entity_type="order",
        entity_id=order.id,
        tenant_id=tenant_id,
        notes=f"{event_type.title()} printed for ticket #{order.ticket_display}.",
    )


def log_exchange_submission(
    order,
    tenant_id: int,
    success: bool,
    details: Optional[str] = None,
) -> AuditLog:
    """Log an exchange submission attempt."""
    action = (
        AuditAction.EXCHANGE_ACCEPTED
        if success
        else AuditAction.EXCHANGE_REJECTED
    )
    return log_action(
        action=action,
        entity_type="order",
        entity_id=order.id,
        tenant_id=tenant_id,
        notes=details or f"Exchange {'accepted' if success else 'rejected'}.",
    )
