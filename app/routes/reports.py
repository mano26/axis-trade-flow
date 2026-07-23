# -*- coding: utf-8 -*-
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
    account_filter = request.args.get("account", "").strip()
    ticket_filter = request.args.get("ticket", "").strip()
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
    if account_filter:
        query = query.filter(Order.account.ilike(f"%{account_filter}%"))
    if ticket_filter:
        # Match on display string (zero-padded) or raw number
        query = query.filter(
            db.or_(
                Order.ticket_display.ilike(f"%{ticket_filter}%"),
                Order.ticket_number == int(ticket_filter) if ticket_filter.isdigit() else db.false(),
            )
        )
    if search_filter:
        # Search raw_input (trade string) — partial match, case-insensitive
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
        account_filter=account_filter,
        ticket_filter=ticket_filter,
        search_filter=search_filter,
        sort_col=sort_col,
        sort_dir=sort_dir,
    )


@reports_bp.route("/eod-summary")
@login_required
def eod_summary():
    from app.models.order import OrderStatus

    report_date = _parse_date(request.args.get("date"), default=date.today())

    # Statuses that represent actual traded volume
    TRADED = {
        OrderStatus.PARTIAL_FILL,
        OrderStatus.FILLED,
        OrderStatus.PARTIAL_CANCELLED,
        OrderStatus.AMENDED,
    }

    all_orders = (
        Order.query
        .filter_by(tenant_id=current_user.tenant_id, trade_date=report_date)
        .filter(Order.deleted_at.is_(None))
        .order_by(Order.ticket_number)
        .all()
    )

    # ── Totals (traded orders only) ───────────────────────────────────────
    total_options_vol = 0
    total_futures_vol = 0
    strategy_counts   = {}

    for order in all_orders:
        if order.status not in TRADED:
            continue

        # Scale by fill ratio so partial fills are counted correctly
        fill_ratio = (order.filled_quantity / order.total_quantity
                      if order.total_quantity else 0)

        for leg in order.legs:
            if leg.option_type is None and leg.strike is None:
                total_futures_vol += round(leg.volume * fill_ratio)
            else:
                total_options_vol += round(leg.volume * fill_ratio)

        # Strategy counts use the package base qty (filled_quantity)
        key = order.strategy or "unknown"
        if key not in strategy_counts:
            strategy_counts[key] = {"count": 0, "volume": 0}
        strategy_counts[key]["count"]  += 1
        strategy_counts[key]["volume"] += order.filled_quantity or 0

    # ── Per-order breakdown rows ──────────────────────────────────────────
    # Each row carries the order-level data plus a list of fill allocations
    # so the template can render both the summary row and the drill-down
    # fill rows for broker/counterparty filtering.
    breakdown = []
    for order in all_orders:
        traded = order.status in TRADED

        if traded:
            fill_ratio = (order.filled_quantity / order.total_quantity
                          if order.total_quantity else 0)
            opts_vol = sum(
                round(l.volume * fill_ratio)
                for l in order.legs
                if not (l.option_type is None and l.strike is None)
            )
            fut_vol = sum(
                round(l.volume * fill_ratio)
                for l in order.legs
                if l.option_type is None and l.strike is None
            )
        else:
            opts_vol = fut_vol = 0

        # Collect all fill→CP allocations for drill-down rows
        cp_rows = []
        all_brokers = []
        all_cps     = []
        for fill in order.fills:
            for cp in fill.counterparties:
                broker = (cp.broker or "").upper()

                # Resolve counterparty symbol (handle old SYM/HOUSE combined)
                sym_raw = cp.symbol or ""
                if cp.cp_house:
                    cp_sym   = sym_raw.upper()
                    cp_house = cp.cp_house.upper()
                elif "/" in sym_raw:
                    cp_sym   = sym_raw.split("/")[0].strip().upper()
                    cp_house = sym_raw.split("/")[1].strip().upper()
                else:
                    cp_sym   = sym_raw.upper()
                    cp_house = ""

                if broker and broker not in all_brokers:
                    all_brokers.append(broker)
                if cp_sym and cp_sym not in all_cps:
                    all_cps.append(cp_sym)

                cp_rows.append({
                    "fill_qty":  cp.quantity,
                    "broker":    broker,
                    "cp":        cp_sym,
                    "cp_house":  cp_house,
                    "bracket":   (cp.bracket or "").upper(),
                })

        breakdown.append({
            "order":    order,
            "traded":   traded,
            "opts_vol": opts_vol,
            "fut_vol":  fut_vol,
            "brokers":  " ".join(all_brokers),   # space-joined for data-attr search
            "cps":      " ".join(all_cps),
            "cp_rows":  cp_rows,
        })

    return render_template(
        "reports/eod_summary.html",
        report_date       = report_date,
        total_orders      = len(all_orders),
        filled_count      = sum(1 for o in all_orders if o.status in TRADED),
        total_options_vol = total_options_vol,
        total_futures_vol = total_futures_vol,
        total_combined    = total_options_vol + total_futures_vol,
        strategy_counts   = strategy_counts,
        breakdown         = breakdown,
    )


def _parse_date(value, default=None):
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return default or date.today()