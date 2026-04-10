# =============================================================================
# Reports Routes
# =============================================================================
from datetime import date, timedelta
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models.order import Order, OrderLeg

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/order-log")
@login_required
def order_log():
    date_from = _parse_date(request.args.get("date_from"), default=date.today())
    date_to = _parse_date(request.args.get("date_to"), default=date.today())
    strategy_filter = request.args.get("strategy", "").strip()
    house_filter = request.args.get("house", "").strip()
    search_filter = request.args.get("search", "").strip()
    sort_col = request.args.get("sort", "ticket_number")
    sort_dir = request.args.get("dir", "desc")

    query = (
        Order.query
        .filter_by(tenant_id=current_user.tenant_id)
        .filter(Order.deleted_at.is_(None))
        .filter(Order.trade_date >= date_from)
        .filter(Order.trade_date <= date_to)
    )

    if strategy_filter:
        query = query.filter(Order.strategy == strategy_filter.lower())
    if house_filter:
        query = query.filter(Order.house.ilike(f"%{house_filter}%"))
    if search_filter:
        query = query.filter(Order.raw_input.ilike(f"%{search_filter}%"))

    sort_column = getattr(Order, sort_col, Order.ticket_number)
    if sort_dir == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    orders = query.all()

    return render_template(
        "reports/order_log.html",
        orders=orders,
        date_from=date_from,
        date_to=date_to,
        strategy_filter=strategy_filter,
        house_filter=house_filter,
        search_filter=search_filter,
        sort_col=sort_col,
        sort_dir=sort_dir,
    )


@reports_bp.route("/eod-summary")
@login_required
def eod_summary():
    report_date = _parse_date(request.args.get("date"), default=date.today())
    orders = (
        Order.query
        .filter_by(tenant_id=current_user.tenant_id, trade_date=report_date)
        .filter(Order.deleted_at.is_(None))
        .all()
    )
    total_orders = len(orders)
    strategy_counts = {}
    for order in orders:
        key = order.strategy or "unknown"
        if key not in strategy_counts:
            strategy_counts[key] = {"count": 0, "volume": 0}
        strategy_counts[key]["count"] += 1
        strategy_counts[key]["volume"] += order.total_quantity

    total_options_vol = 0
    total_futures_vol = 0
    for order in orders:
        for leg in order.legs:
            if leg.option_type is None and leg.strike is None:
                total_futures_vol += leg.volume
            else:
                total_options_vol += leg.volume

    return render_template(
        "reports/eod_summary.html",
        report_date=report_date,
        total_orders=total_orders,
        total_options_vol=total_options_vol,
        total_futures_vol=total_futures_vol,
        strategy_counts=strategy_counts,
        orders=orders,
    )


def _parse_date(value, default=None):
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return default or date.today()