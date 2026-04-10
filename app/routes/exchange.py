# =============================================================================
# Exchange Reporting Routes (Rithmic Stub)
# =============================================================================
# Handles submission of filled trades to CME via Rithmic API through
# Dorman Trading. Currently stubbed — all submissions return simulated
# responses.
#
# URL prefix: /exchange
#
# REGULATORY NOTE: Exchange reporting is a regulatory obligation for
# OTC/voice-brokered trades. Failed submissions must be retried or
# escalated. All submission attempts are logged regardless of outcome.
# =============================================================================

from flask import Blueprint, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.services.rithmic_client import RithmicClient
from app.services.card_generator import build_card_data_snapshot
from app.services import audit_service

exchange_bp = Blueprint("exchange", __name__)


@exchange_bp.route("/order/<int:order_id>/submit", methods=["POST"])
@login_required
def submit_to_exchange(order_id: int):
    """
    Submit a filled trade to CME via Rithmic.

    Prerequisites:
      - Order must be FILLED, PARTIAL_FILL, or PARTIAL_CANCELLED
      - All fills must be allocated
      - All prices must be entered

    The order transitions to REPORTED upon submission, then to
    REPORT_ACCEPTED or REPORT_FAILED based on the exchange response.
    """
    order = Order.query.filter_by(
        id=order_id, tenant_id=current_user.tenant_id,
    ).first_or_404()

    # Validate order can be submitted
    submittable = {OrderStatus.FILLED, OrderStatus.PARTIAL_FILL, OrderStatus.PARTIAL_CANCELLED}
    if order.status not in submittable:
        flash(f"Cannot submit an order in '{order.status}' status.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    # Transition to REPORTED
    old_status = order.status
    try:
        order.transition_to(OrderStatus.REPORTED)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("orders.detail", order_id=order.id))

    # Build submission data
    order_data = build_card_data_snapshot(order)
    fill_data = {
        "fills": [
            {
                "fill_id": f.id,
                "fill_quantity": f.fill_quantity,
                "prices": [
                    {"leg_index": lp.leg_index, "price": lp.price}
                    for lp in f.leg_prices
                ],
            }
            for f in order.fills
        ],
    }

    # Submit via Rithmic client (STUBBED)
    client = RithmicClient()
    result = client.submit_trade_report(order_data, fill_data)

    # Handle response
    if result.success:
        order.transition_to(OrderStatus.REPORT_ACCEPTED)
        audit_service.log_exchange_submission(
            order, current_user.tenant_id, success=True,
            details=f"Reference: {result.reference_id}",
        )
        flash(
            f"Trade reported to exchange. Reference: {result.reference_id}",
            "success",
        )
    else:
        order.transition_to(OrderStatus.REPORT_FAILED)
        audit_service.log_exchange_submission(
            order, current_user.tenant_id, success=False,
            details=result.error_message,
        )
        flash(
            f"Exchange submission failed: {result.error_message}",
            "danger",
        )

    audit_service.log_order_status_change(
        order, current_user.tenant_id, old_status, order.status,
    )
    db.session.commit()

    return redirect(url_for("orders.detail", order_id=order.id))
