# =============================================================================
# Order Routes
# =============================================================================
# Handles the full order lifecycle: creation, modification, cancellation,
# fill recording, price entry, counterparty entry — all from the detail page.
#
# URL prefix: /orders
#
# REGULATORY NOTE: Every state transition and modification is logged via
# the audit service. Orders are never hard-deleted; the cancel action sets
# status to CANCELLED with a full audit trail.
# =============================================================================

from datetime import date
from flask import (
    Blueprint, render_template, redirect, url_for, flash,
    request, jsonify,
)
from flask_login import login_required, current_user
from app.extensions import db
from app.models.order import Order, OrderLeg, OrderStatus
from app.models.fill import Fill, FillLegPrice, FillCounterparty, AllocationStatus
from app.models.tenant import Tenant
from app.services.trade_parser import parse_trade_input, ParseError
from app.services.strategy_handlers import build_legs
from app.services.validation import (
    validate_fill_prices,
    validate_counterparty_quantities,
    validate_counterparty_completeness,
    ValidationError,
)
from app.services import audit_service

orders_bp = Blueprint("orders", __name__)


@orders_bp.route("/")
@login_required
def index():
    """
    Order entry and daily blotter page.

    Displays the trade entry form and a list of today's orders for the
    current user's tenant.
    """
    today = date.today()
    orders = (
        Order.query
        .filter_by(tenant_id=current_user.tenant_id, trade_date=today)
        .filter(Order.deleted_at.is_(None))
        .order_by(Order.ticket_number.desc())
        .all()
    )
    return render_template("orders/index.html", orders=orders, today=today)


@orders_bp.route("/create", methods=["POST"])
@login_required
def create():
    """
    Parse a trade string and create a new order.
    Redirects to the order detail (workspace) page after creation.
    """
    raw_input = request.form.get("trade_string", "").strip()
    if not raw_input:
        flash("Please enter a trade string.", "warning")
        return redirect(url_for("orders.index"))

    try:
        trade_parts = parse_trade_input(raw_input)
        if not trade_parts:
            flash("No valid trade legs found.", "danger")
            return redirect(url_for("orders.index"))

        ticket_num = _get_next_ticket_number(current_user.tenant_id)

        all_legs = []
        primary_strategy = trade_parts[0].strategy
        direction = trade_parts[0].direction_side
        total_volume = trade_parts[0].volume
        package_premium = trade_parts[0].premium

        for part in trade_parts:
            legs = build_legs(part)
            all_legs.extend(legs)

        order = Order(
            tenant_id=current_user.tenant_id,
            ticket_number=ticket_num,
            ticket_display=f"{ticket_num:04d}",
            trade_date=date.today(),
            raw_input=raw_input,
            direction=direction,
            total_quantity=total_volume,
            package_premium=package_premium,
            strategy=primary_strategy,
            status=OrderStatus.OPEN,
            created_by_id=current_user.id,
        )
        db.session.add(order)
        db.session.flush()

        for idx, leg_data in enumerate(all_legs):
            leg = OrderLeg(
                order_id=order.id,
                leg_index=idx,
                side=leg_data["side"],
                volume=leg_data["volume"],
                market=leg_data["market"],
                contract_type=leg_data["contract_type"],
                expiry=leg_data["expiry"],
                strike=leg_data.get("strike"),
                option_type=leg_data.get("option_type"),
                price=leg_data.get("price"),
                mo_card_code=leg_data.get("mo_card_code"),
                package_premium=leg_data.get("package_premium"),
                suppress_premium=leg_data.get("suppress_premium", False),
            )
            db.session.add(leg)

        audit_service.log_order_created(order, current_user.tenant_id)
        db.session.commit()
        flash(f"Order #{order.ticket_display} created — {len(all_legs)} leg(s).", "success")
        return redirect(url_for("orders.detail", order_id=order.id))

    except ParseError as e:
        flash(f"Parse error: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error creating order: {e}", "danger")

    return redirect(url_for("orders.index"))


@orders_bp.route("/<int:order_id>")
@login_required
def detail(order_id: int):
    order = _get_order_or_404(order_id)
    latest_fill = order.fills[-1] if order.fills else None
    # Load lookup values for dropdowns
    from app.models.lookup import get_lookup_values, LookupType
    lookups = {
        lt: get_lookup_values(current_user.tenant_id, lt)
        for lt in LookupType.ALL
    }
    return render_template(
        "orders/detail.html",
        order=order,
        latest_fill=latest_fill,
        lookups=lookups,
    )


# =========================================================================
# Inline Actions (all POST back to the detail page)
# =========================================================================

@orders_bp.route("/<int:order_id>/record-fill", methods=["POST"])
@login_required
def record_fill(order_id: int):
    """
    Record a fill against an order. Posted from the detail page.
    """
    order = _get_order_or_404(order_id)

    if order.status not in (OrderStatus.OPEN, OrderStatus.PARTIAL_FILL):
        flash(f"Cannot fill an order in '{order.status}' status.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    try:
        fill_qty = int(request.form.get("fill_quantity", 0))
        if fill_qty <= 0:
            flash("Fill quantity must be positive.", "warning")
            return redirect(url_for("orders.detail", order_id=order.id))

        if fill_qty > order.remaining_quantity:
            flash(
                f"Fill quantity ({fill_qty}) exceeds remaining ({order.remaining_quantity}).",
                "warning",
            )
            return redirect(url_for("orders.detail", order_id=order.id))

        fill = Fill(
            tenant_id=current_user.tenant_id,
            order_id=order.id,
            fill_quantity=fill_qty,
            allocation_status=AllocationStatus.PENDING,
            created_by_id=current_user.id,
        )
        db.session.add(fill)

        old_status = order.status
        order.filled_quantity += fill_qty

        if order.remaining_quantity == 0:
            order.transition_to(OrderStatus.FILLED)
        elif order.status == OrderStatus.OPEN:
            order.transition_to(OrderStatus.PARTIAL_FILL)

        db.session.flush()
        audit_service.log_fill_created(fill, current_user.tenant_id)
        if order.status != old_status:
            audit_service.log_order_status_change(
                order, current_user.tenant_id, old_status, order.status,
            )

        db.session.commit()
        flash(f"Fill recorded: {fill_qty} contracts. Remaining: {order.remaining_quantity}.", "success")

    except Exception as e:
        db.session.rollback()
        flash(f"Error recording fill: {e}", "danger")

    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/save-prices", methods=["POST"])
@login_required
def save_prices(order_id: int):
    """
    Save leg prices from the inline price fields on the detail page.
    Validates via price reconciliation (hard block).
    """
    order = _get_order_or_404(order_id)

    # Find or create a fill to attach prices to
    if not order.fills:
        flash("Please record a fill before entering prices.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    fill = order.fills[-1]  # Most recent fill

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

        # Clear existing and save new
        FillLegPrice.query.filter_by(fill_id=fill.id).delete()
        for lp in leg_prices:
            db.session.add(lp)

        # Also update OrderLeg.price for display
        price_map = {lp.leg_index: lp.price for lp in leg_prices}
        for leg in order.legs:
            if leg.leg_index in price_map:
                leg.price = price_map[leg.leg_index]

        db.session.commit()
        flash("Prices saved and validated.", "success")

    except ValidationError as e:
        db.session.rollback()
        flash(f"Price validation failed: {'; '.join(e.errors)}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving prices: {e}", "danger")

    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/save-counterparties", methods=["POST"])
@login_required
def save_counterparties(order_id: int):
    """
    Save counterparty allocations from the inline form on the detail page.
    Also saves house/account.
    """
    order = _get_order_or_404(order_id)

    if not order.fills:
        flash("Please record a fill before entering counterparties.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    fill = order.fills[-1]  # Most recent fill

    try:
        # Save house/account
        house = request.form.get("house", "").strip()
        account = request.form.get("account", "").strip()
        if house:
            order.house = house
        if account:
            order.account = account

        counterparties = []
        for i in range(20):
            qty_str = request.form.get(f"cp_qty_{i}", "").strip()
            broker = request.form.get(f"cp_broker_{i}", "").strip()
            symbol = request.form.get(f"cp_symbol_{i}", "").strip()
            bracket = request.form.get(f"cp_bracket_{i}", "").strip()
            notes = request.form.get(f"cp_notes_{i}", "").strip()

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
            flash("No counterparties entered. Fill remains in pending allocation.", "info")
            db.session.commit()  # Still save house/account
            return redirect(url_for("orders.detail", order_id=order.id))

        validate_counterparty_completeness(counterparties)
        validate_counterparty_quantities(fill, counterparties)

        FillCounterparty.query.filter_by(fill_id=fill.id).delete()
        for cp in counterparties:
            db.session.add(cp)

        fill.allocation_status = AllocationStatus.ALLOCATED

        db.session.commit()
        flash("Counterparties saved. Allocation complete.", "success")

    except ValidationError as e:
        db.session.rollback()
        flash(f"Validation failed: {'; '.join(e.errors)}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving counterparties: {e}", "danger")

    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/cancel", methods=["POST"])
@login_required
def cancel(order_id: int):
    """Cancel an unfilled order or the remaining balance of a partial fill."""
    order = _get_order_or_404(order_id)
    old_status = order.status

    try:
        if order.status == OrderStatus.OPEN:
            order.transition_to(OrderStatus.CANCELLED)
        elif order.status == OrderStatus.PARTIAL_FILL:
            order.transition_to(OrderStatus.PARTIAL_CANCELLED)
        else:
            flash(f"Cannot cancel an order in '{order.status}' status.", "warning")
            return redirect(url_for("orders.detail", order_id=order.id))

        audit_service.log_order_status_change(
            order, current_user.tenant_id, old_status, order.status,
            notes="Order cancelled by user.",
        )
        db.session.commit()
        flash(f"Order #{order.ticket_display} cancelled.", "info")

    except ValueError as e:
        flash(str(e), "danger")

    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/modify", methods=["GET", "POST"])
@login_required
def modify(order_id: int):
    """Modify an unfilled order (same ticket number)."""
    order = _get_order_or_404(order_id)

    if order.status != OrderStatus.OPEN:
        flash("Only unfilled (OPEN) orders can be modified.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    if request.method == "GET":
        return render_template("orders/modify.html", order=order)

    new_input = request.form.get("trade_string", "").strip()
    if not new_input:
        flash("Please enter a trade string.", "warning")
        return redirect(url_for("orders.modify", order_id=order.id))

    try:
        before = {
            "raw_input": order.raw_input,
            "direction": order.direction,
            "total_quantity": order.total_quantity,
            "strategy": order.strategy,
        }

        trade_parts = parse_trade_input(new_input)
        all_legs = []
        for part in trade_parts:
            all_legs.extend(build_legs(part))

        for leg in order.legs:
            db.session.delete(leg)

        order.raw_input = new_input
        order.direction = trade_parts[0].direction_side
        order.total_quantity = trade_parts[0].volume
        order.package_premium = trade_parts[0].premium
        order.strategy = trade_parts[0].strategy

        for idx, leg_data in enumerate(all_legs):
            leg = OrderLeg(
                order_id=order.id, leg_index=idx,
                side=leg_data["side"], volume=leg_data["volume"],
                market=leg_data["market"], contract_type=leg_data["contract_type"],
                expiry=leg_data["expiry"], strike=leg_data.get("strike"),
                option_type=leg_data.get("option_type"), price=leg_data.get("price"),
                mo_card_code=leg_data.get("mo_card_code"),
                package_premium=leg_data.get("package_premium"),
                suppress_premium=leg_data.get("suppress_premium", False),
            )
            db.session.add(leg)

        after = {
            "raw_input": order.raw_input,
            "direction": order.direction,
            "total_quantity": order.total_quantity,
            "strategy": order.strategy,
        }

        audit_service.log_order_modified(order, current_user.tenant_id, before, after)
        db.session.commit()
        flash(f"Order #{order.ticket_display} modified.", "success")

    except ParseError as e:
        flash(f"Parse error: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error modifying order: {e}", "danger")

    return redirect(url_for("orders.detail", order_id=order.id))


# =========================================================================
# Helpers
# =========================================================================

def _get_order_or_404(order_id: int) -> Order:
    """Fetch an order by ID, scoped to the current user's tenant."""
    return Order.query.filter_by(
        id=order_id, tenant_id=current_user.tenant_id,
    ).first_or_404()


def _get_next_ticket_number(tenant_id: int) -> int:
    """Get the next sequential ticket number for a tenant. Resets daily."""
    tenant = db.session.get(Tenant, tenant_id)
    today = date.today()
    if tenant.ticket_date != today:
        tenant.current_ticket_number = 0
        tenant.ticket_date = today
    tenant.current_ticket_number += 1
    if tenant.current_ticket_number > 9999:
        tenant.current_ticket_number = 1
    db.session.flush()
    return tenant.current_ticket_number
