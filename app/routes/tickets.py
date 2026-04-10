# =============================================================================
# Ticket Generation Routes
# =============================================================================
from flask import Blueprint, render_template, redirect, url_for, flash, make_response
from flask_login import login_required, current_user
from app.extensions import db
from app.models.order import Order
from app.models.print_event import PrintEvent, PrintEventType
from app.services.ticket_generator import generate_ticket_html, build_ticket_data_snapshot
from app.services.validation import validate_before_generate, ValidationError
from app.services import audit_service

tickets_bp = Blueprint("tickets", __name__)


@tickets_bp.route("/order/<int:order_id>/generate")
@login_required
def generate(order_id):
    order = Order.query.filter_by(
        id=order_id, tenant_id=current_user.tenant_id,
    ).first_or_404()

    try:
        validate_before_generate(order)
    except ValidationError as e:
        flash(f"Cannot generate ticket: {'; '.join(e.errors)}", "danger")
        return redirect(url_for("orders.detail", order_id=order.id))

    # Create print event
    snapshot = build_ticket_data_snapshot(order)
    print_event = PrintEvent(
        tenant_id=current_user.tenant_id,
        order_id=order.id,
        event_type=PrintEventType.TICKET,
        printed_by_id=current_user.id,
        data_snapshot=snapshot,
    )
    db.session.add(print_event)
    audit_service.log_print_event(order, current_user.tenant_id, "ticket")
    db.session.commit()

    # Generate and return HTML
    html = generate_ticket_html(order)
    response = make_response(html)
    response.headers["Content-Type"] = "text/html"
    return response