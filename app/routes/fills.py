# =============================================================================
# Fill Routes
# =============================================================================
# Handles fill entry, price entry, counterparty allocation, and amendments.
#
# URL prefix: /fills
#
# WORKFLOW:
# 1. User records a fill (quantity) against an order
# 2. User enters per-leg prices (validated via price reconciliation)
# 3. User enters counterparties (validated: qty must match fill qty)
# 4. Once all counterparties are entered, allocation status → ALLOCATED
#
# REGULATORY NOTE: Fill prices are hard-blocked if they don't reconcile.
# All price and counterparty changes are logged in the audit trail.
# =============================================================================

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.models.fill import Fill, FillLegPrice, FillCounterparty, AllocationStatus
from app.services.validation import (
    validate_fill_prices,
    validate_counterparty_quantities,
    validate_counterparty_completeness,
    ValidationError,
)
from app.services import audit_service

fills_bp = Blueprint("fills", __name__)


@fills_bp.route("/order/<int:order_id>/create", methods=["GET", "POST"])
@login_required
def create(order_id: int):
    """
    Record a fill against an order.

    GET:  Show the fill entry form with order details.
    POST: Create a Fill record and transition the order to PARTIAL_FILL
          or FILLED.
    """
    order = _get_order_or_404(order_id)

    # Validate order can receive a fill
    if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIAL_FILL):
        flash(f"Cannot fill an order in '{order.status}' status.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    if request.method == "GET":
        return render_template("orders/fill_entry.html", order=order)

    # POST: Record the fill
    try:
        fill_qty = int(request.form.get("fill_quantity", 0))
        if fill_qty <= 0:
            flash("Fill quantity must be positive.", "warning")
            return redirect(url_for("fills.create", order_id=order.id))

        if fill_qty > order.remaining_quantity:
            flash(
                f"Fill quantity ({fill_qty}) exceeds remaining "
                f"({order.remaining_quantity}).",
                "warning",
            )
            return redirect(url_for("fills.create", order_id=order.id))

        # Create the fill
        fill = Fill(
            tenant_id=current_user.tenant_id,
            order_id=order.id,
            fill_quantity=fill_qty,
            allocation_status=AllocationStatus.PENDING,
            created_by_id=current_user.id,
        )
        db.session.add(fill)

        # Update order quantities and status
        old_status = order.status
        order.filled_quantity += fill_qty

        if order.remaining_quantity == 0:
            order.transition_to(OrderStatus.FILLED)
        elif order.status == OrderStatus.OPEN:
            order.transition_to(OrderStatus.PARTIAL_FILL)
        # If already PARTIAL_FILL, stays PARTIAL_FILL

        db.session.flush()

        # Audit trail
        audit_service.log_fill_created(fill, current_user.tenant_id)
        if order.status != old_status:
            audit_service.log_order_status_change(
                order, current_user.tenant_id, old_status, order.status,
            )

        db.session.commit()
        flash(
            f"Fill recorded: {fill_qty} contracts. "
            f"Remaining: {order.remaining_quantity}.",
            "success",
        )
        return redirect(url_for("fills.enter_prices", fill_id=fill.id))

    except Exception as e:
        db.session.rollback()
        flash(f"Error recording fill: {e}", "danger")
        return redirect(url_for("fills.create", order_id=order.id))


@fills_bp.route("/<int:fill_id>/prices", methods=["GET", "POST"])
@login_required
def enter_prices(fill_id: int):
    """
    Enter per-leg fill prices.

    GET:  Show the price entry form with one field per leg.
    POST: Validate prices via reconciliation and save.

    REGULATORY NOTE: Price reconciliation is a HARD BLOCK. If leg prices
    do not net to the package premium, the save is rejected and the user
    must correct the prices.
    """
    fill = _get_fill_or_404(fill_id)
    order = fill.order

    if request.method == "GET":
        return render_template("orders/price_entry.html", fill=fill, order=order)

    # POST: Save prices
    try:
        leg_prices = []
        for leg in order.legs:
            price_str = request.form.get(f"price_{leg.leg_index}", "").strip()
            if price_str:
                leg_price = FillLegPrice(
                    fill_id=fill.id,
                    leg_index=leg.leg_index,
                    price=float(price_str),
                )
                leg_prices.append(leg_price)

        # Validate price reconciliation (HARD BLOCK)
        validate_fill_prices(order, fill, leg_prices)

        # Clear existing prices (for re-entry) and save new ones
        FillLegPrice.query.filter_by(fill_id=fill.id).delete()
        for lp in leg_prices:
            db.session.add(lp)

        # Also update the OrderLeg.price field for display
        price_map = {lp.leg_index: lp.price for lp in leg_prices}
        for leg in order.legs:
            if leg.leg_index in price_map:
                leg.price = price_map[leg.leg_index]

        db.session.commit()
        flash("Prices saved and validated.", "success")
        return redirect(url_for("fills.enter_counterparties", fill_id=fill.id))

    except ValidationError as e:
        flash(f"Price validation failed: {'; '.join(e.errors)}", "danger")
        return redirect(url_for("fills.enter_prices", fill_id=fill.id))
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving prices: {e}", "danger")
        return redirect(url_for("fills.enter_prices", fill_id=fill.id))


@fills_bp.route("/<int:fill_id>/counterparties", methods=["GET", "POST"])
@login_required
def enter_counterparties(fill_id: int):
    """
    Enter counterparty allocations for a fill.

    GET:  Show the counterparty entry form.
    POST: Validate and save counterparties. Transition allocation status
          to ALLOCATED if quantities match.
    """
    fill = _get_fill_or_404(fill_id)
    order = fill.order

    if request.method == "GET":
        return render_template(
            "orders/counterparty_entry.html", fill=fill, order=order,
        )

    # POST: Save counterparties
    try:
        counterparties = []
        # Read counterparty rows from form (up to 20 rows)
        for i in range(20):
            qty_str = request.form.get(f"cp_qty_{i}", "").strip()
            broker = request.form.get(f"cp_broker_{i}", "").strip()
            symbol = request.form.get(f"cp_symbol_{i}", "").strip()
            bracket = request.form.get(f"cp_bracket_{i}", "").strip()
            notes = request.form.get(f"cp_notes_{i}", "").strip()

            # Skip empty rows
            if not any([qty_str, broker, symbol, bracket]):
                continue

            cp = FillCounterparty(
                fill_id=fill.id,
                quantity=int(qty_str) if qty_str else 0,
                broker=broker,
                symbol=symbol,
                bracket=bracket,
                notes=notes or None,
            )
            counterparties.append(cp)

        if not counterparties:
            # Allow pending allocation (broker forgot scenario)
            flash(
                "No counterparties entered. Fill remains in pending allocation.",
                "info",
            )
            return redirect(url_for("orders.detail", order_id=order.id))

        # Validate completeness
        validate_counterparty_completeness(counterparties)

        # Validate quantities
        validate_counterparty_quantities(fill, counterparties)

        # Clear existing and save new
        FillCounterparty.query.filter_by(fill_id=fill.id).delete()
        for cp in counterparties:
            db.session.add(cp)

        # Transition to ALLOCATED
        fill.allocation_status = AllocationStatus.ALLOCATED

        db.session.commit()
        flash("Counterparties saved. Allocation complete.", "success")
        return redirect(url_for("orders.detail", order_id=order.id))

    except ValidationError as e:
        flash(f"Validation failed: {'; '.join(e.errors)}", "danger")
        return redirect(url_for("fills.enter_counterparties", fill_id=fill.id))
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving counterparties: {e}", "danger")
        return redirect(url_for("fills.enter_counterparties", fill_id=fill.id))


# =========================================================================
# Helpers
# =========================================================================

def _get_order_or_404(order_id: int) -> Order:
    """Fetch an order scoped to the current user's tenant."""
    return Order.query.filter_by(
        id=order_id, tenant_id=current_user.tenant_id,
    ).first_or_404()


def _get_fill_or_404(fill_id: int) -> Fill:
    """Fetch a fill scoped to the current user's tenant."""
    return Fill.query.filter_by(
        id=fill_id, tenant_id=current_user.tenant_id,
    ).first_or_404()
