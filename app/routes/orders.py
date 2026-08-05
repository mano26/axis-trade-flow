# -*- coding: utf-8 -*-
# =============================================================================
# Order Routes
# =============================================================================
# Handles the full order lifecycle including generic mode.
# URL prefix: /orders
# =============================================================================

from datetime import date, datetime, timezone
import re
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
    today = date.today()
    orders = (
        Order.query
        .filter_by(tenant_id=current_user.tenant_id, trade_date=today)
        .filter(Order.deleted_at.is_(None))
        .order_by(Order.ticket_number.desc())
        .all()
    )
    from app.models.lookup import get_lookup_values, LookupType
    lookups = {
        lt: get_lookup_values(current_user.tenant_id, lt)
        for lt in LookupType.ALL
    }
    return render_template("orders/index.html", orders=orders, today=today, lookups=lookups)


@orders_bp.route("/create", methods=["POST"])
@login_required
def create():
    raw_input = request.form.get("trade_string", "").strip()
    is_generic = request.form.get("is_generic") == "1"
    quarter_tick_confirmed = request.form.get("quarter_tick_confirmed") == "1"
    entry_house = request.form.get("house", "").strip()
    entry_account = request.form.get("account", "").strip()

    if not raw_input:
        flash("Please enter a trade string.", "warning")
        return redirect(url_for("orders.index"))

    # Optional manual ticket number override (super admin only). When set,
    # this order takes that exact number instead of auto-increment — used
    # to set the day's starting ticket # directly from the entry form.
    # Applies once; subsequent orders resume normal auto-increment from there.
    ticket_override_str = request.form.get("ticket_override", "").strip()
    forced_ticket_num = None
    if ticket_override_str and current_user.is_super():
        try:
            forced_ticket_num = int(ticket_override_str)
            if not 1 <= forced_ticket_num <= 9999:
                raise ValueError
        except ValueError:
            flash("Ticket # must be between 1 and 9999.", "warning")
            return redirect(url_for("orders.index"))

        existing = db.session.execute(
            db.select(Order.id)
            .where(Order.tenant_id == current_user.tenant_id)
            .where(Order.trade_date == date.today())
            .where(Order.ticket_number == forced_ticket_num)
        ).first()
        if existing:
            flash(f"Ticket #{forced_ticket_num:04d} is already in use today.", "warning")
            return redirect(url_for("orders.index"))

    try:
        # Extract price/direction/volume from trade string
        # (works for both generic and parsed modes)
        direction, volume, premium = _extract_price_info(raw_input)

        # Check for 0.25 tick increment
        premium_ticks = premium * 100  # Convert to ticks
        is_quarter_tick = (premium_ticks % 0.5) != 0 and (premium_ticks % 0.25) == 0

        if is_quarter_tick and not quarter_tick_confirmed and not is_generic:
            # Return to form with popup trigger
            flash("QUARTER_TICK_CHECK", "quarter_tick")
            return redirect(url_for("orders.index"))

        if forced_ticket_num is not None:
            ticket_num = forced_ticket_num
            tenant = db.session.get(Tenant, current_user.tenant_id)
            tenant.current_ticket_number = forced_ticket_num
        else:
            ticket_num = _get_next_ticket_number(current_user.tenant_id)

        if is_generic:
            # Generic mode: store trade string, blank legs
            order = Order(
                tenant_id=current_user.tenant_id,
                ticket_number=ticket_num,
                ticket_display=f"{ticket_num:04d}",
                trade_date=date.today(),
                raw_input=raw_input,
                direction=direction,
                total_quantity=volume,
                package_premium=premium,
                strategy="generic",
                is_generic=True,
                status=OrderStatus.OPEN,
                created_by_id=current_user.id,
                house=entry_house or None,
                account=entry_account or None,
            )
            db.session.add(order)
            db.session.flush()
            audit_service.log_order_created(order, current_user.tenant_id)
            db.session.commit()
            flash(f"Generic order #{order.ticket_display} created. Enter legs manually.", "success")
            return redirect(url_for("orders.detail", order_id=order.id))
        else:
            # Parsed mode
            trade_parts = parse_trade_input(raw_input)
            if not trade_parts:
                flash("No valid trade legs found.", "danger")
                return redirect(url_for("orders.index"))

            all_legs = []
            primary_strategy = trade_parts[0].strategy
            # direction comes from the price format token (/ = buy, @ = sell),
            # NOT from trade_parts[0].direction_side — that can be flipped by a
            # direction hint like (SFRU6) and must not affect the order-level field.
            # _extract_price_info is already called above for the tick check.
            direction, total_volume, package_premium = _extract_price_info(raw_input)

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
                is_generic=False,
                status=OrderStatus.OPEN,
                created_by_id=current_user.id,
                house=entry_house or None,
                account=entry_account or None,
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
                    cvd_raw_volume=leg_data.get("cvd_raw_volume", None),
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
def detail(order_id):
    order = _get_order_or_404(order_id)
    latest_fill = order.fills[-1] if order.fills else None
    from app.models.lookup import get_lookup_values, LookupType
    lookups = {
        lt: get_lookup_values(current_user.tenant_id, lt)
        for lt in LookupType.ALL
    }

    # Build display_legs — always use leg.volume as the editable value.
    # The Save Legs form must always submit the full order size so that
    # clicking Save Legs never permanently corrupts stored leg volumes.
    # A read-only filled qty hint is shown separately in the summary header.
    display_legs = []
    for leg in order.legs:
        d = {
            "leg_index": leg.leg_index,
            "side": leg.side,
            "volume": leg.volume,
            "display_volume": leg.volume,   # always full size — never ratio-adjusted
            "market": leg.market,
            "contract_type": leg.contract_type,
            "expiry": leg.expiry,
            "strike": leg.strike,
            "option_type": leg.option_type,
            "price": leg.price,
        }
        display_legs.append(d)

    # Check if prices are entered on the latest fill (for blocking CP save)
    has_prices = bool(latest_fill and latest_fill.leg_prices)

    # Collect all fills with their counterparties for display — not just the latest.
    # Each entry: {fill, counterparties, has_prices}
    all_fills_display = []
    for fill in order.fills:
        all_fills_display.append({
            "fill": fill,
            "counterparties": fill.counterparties,
            "has_prices": bool(fill.leg_prices),
        })

    # Oldest pending fill — prices should be entered in chronological order.
    # The prices form targets this fill so earlier partial fills get allocated first.
    oldest_pending_fill = next(
        (f for f in order.fills if f.allocation_status != "allocated"),
        latest_fill,
    )

    # GCD of option leg volumes — used to scale CP quantities to per-leg lot counts
    # e.g. for a 2X3 CS (legs 500/750), gcd=250, so 250 packages → 500 BUY / 750 SELL
    from math import gcd as _gcd
    from functools import reduce as _reduce
    _opt_vols = [l.volume for l in order.legs
                 if l.option_type is not None and l.volume and l.volume > 0]
    gcd_opt_vol = _reduce(_gcd, _opt_vols) if _opt_vols else 1

    return render_template(
        "orders/detail.html",
        order=order,
        latest_fill=latest_fill,
        oldest_pending_fill=oldest_pending_fill,
        all_fills_display=all_fills_display,
        lookups=lookups,
        display_legs=display_legs,
        has_prices=has_prices,
        gcd_opt_vol=gcd_opt_vol,
    )


# =========================================================================
# Inline Actions
# =========================================================================

@orders_bp.route("/<int:order_id>/record-fill", methods=["POST"])
@login_required
def record_fill(order_id):
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
            flash(f"Fill qty ({fill_qty}) exceeds remaining ({order.remaining_quantity}).", "warning")
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


@orders_bp.route("/<int:order_id>/save-legs", methods=["POST"])
@login_required
def save_legs(order_id):
    """
    Save editable leg data from the detail page.
    Only valid for generic orders — parsed orders use toggle_leg_side
    for B/S corrections and Modify Order for structural changes.
    """
    order = _get_order_or_404(order_id)
    if not order.is_generic:
        flash("Leg editing is only available for generic orders. Use Modify Order to change a parsed trade.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))
    try:
        # Capture existing prices before deleting legs (CVD futures price etc.)
        existing_prices = {leg.leg_index: leg.price for leg in order.legs if leg.price is not None}

        # Delete existing legs
        for leg in order.legs:
            db.session.delete(leg)
        db.session.flush()

        # Read legs from form
        leg_index = 0
        for i in range(30):  # Support up to 30 rows
            side = request.form.get(f"leg_side_{i}", "").strip()
            vol_str = request.form.get(f"leg_volume_{i}", "").strip()
            market = request.form.get(f"leg_market_{i}", "").strip() or "CME"
            contract = request.form.get(f"leg_contract_{i}", "").strip()
            expiry = request.form.get(f"leg_expiry_{i}", "").strip()
            strike_str = request.form.get(f"leg_strike_{i}", "").strip()
            opt_type = request.form.get(f"leg_opttype_{i}", "").strip()

            # A valid leg needs volume AND at least contract or strike
            if not vol_str:
                continue
            volume = int(vol_str)
            if volume <= 0:
                continue
            if not contract and not strike_str and not expiry:
                continue

            strike = float(strike_str) if strike_str else None
            preserved_price = existing_prices.get(i)

            leg = OrderLeg(
                order_id=order.id,
                leg_index=leg_index,
                side=side.upper() if side else "B",
                volume=volume,
                market=market.upper(),
                contract_type=contract.upper() if contract else "SR3",
                expiry=expiry.upper() if expiry else "",
                strike=strike,
                option_type=opt_type.upper() if opt_type else None,
                price=preserved_price,
                mo_card_code=expiry.upper() if expiry else "",
                package_premium=order.package_premium,
                suppress_premium=False,
            )
            db.session.add(leg)
            leg_index += 1

        # Do NOT overwrite total_quantity from leg volumes.
        # total_quantity is set at order creation from the trade string price format
        # (e.g. 4.25/1000 → 1000) and represents the overall order size.
        # A generic order can have multiple buy legs at different prices to represent
        # an average fill, so the first-leg volume is not a reliable proxy.

        db.session.commit()
        flash(f"Legs saved — {leg_index} leg(s).", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving legs: {e}", "danger")
    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/legs/<int:leg_index>/side", methods=["POST"])
@login_required
def toggle_leg_side(order_id, leg_index):
    """
    Toggle the B/S direction of a single parsed-order leg.
    Accepts JSON: {"side": "B"} or {"side": "S"}.
    Returns JSON: {"ok": true, "side": "B"}.
    This is the only structural edit allowed on parsed order legs.
    """
    order = _get_order_or_404(order_id)
    if order.is_generic:
        return jsonify({"ok": False, "error": "Use Save Legs for generic orders."}), 400

    data = request.get_json(silent=True) or {}
    new_side = (data.get("side") or "").upper().strip()
    if new_side not in ("B", "S"):
        return jsonify({"ok": False, "error": "side must be B or S"}), 400

    leg = OrderLeg.query.filter_by(
        order_id=order.id, leg_index=leg_index
    ).first_or_404()

    old_side = leg.side
    if old_side == new_side:
        return jsonify({"ok": True, "side": new_side})

    try:
        leg.side = new_side
        audit_service.log_action(
            action="leg_side_toggled",
            entity_type="order",
            entity_id=order.id,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            before_value={"leg_index": leg_index, "side": old_side},
            after_value={"leg_index": leg_index, "side": new_side},
        )
        db.session.commit()
        return jsonify({"ok": True, "side": new_side})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500


@orders_bp.route("/<int:order_id>/save-prices", methods=["POST"])
@login_required
def save_prices(order_id):
    order = _get_order_or_404(order_id)
    if not order.fills:
        flash("Please record a fill before entering prices.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))
    # Use fill_id from form if provided so prices can be saved for any pending fill.
    fill_id_str = request.form.get("fill_id", "").strip()
    if fill_id_str:
        fill = Fill.query.filter_by(
            id=int(fill_id_str), order_id=order.id
        ).first_or_404()
    else:
        fill = order.fills[-1]
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

        # For generic orders, validate using qty*price net calculation
        if order.is_generic:
            price_override = request.form.get("price_override_confirmed") == "1"
            discrepancy = _validate_generic_prices(order, leg_prices, override=price_override)
            if price_override and discrepancy > 0.000001:
                audit_service.log_action(
                    action="price_reconciliation_overridden",
                    entity_type="fill",
                    entity_id=fill.id,
                    tenant_id=current_user.tenant_id,
                    user_id=current_user.id,
                    notes=(
                        f"Order #{order.ticket_display}: trader confirmed odd-lot "
                        f"pricing override. Discrepancy: {discrepancy:.4f} "
                        f"(expected {order.package_premium:.4f})."
                    ),
                )
        else:
            validate_fill_prices(order, fill, leg_prices)

        FillLegPrice.query.filter_by(fill_id=fill.id).delete()
        for lp in leg_prices:
            db.session.add(lp)

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
def save_counterparties(order_id):
    order = _get_order_or_404(order_id)
    if not order.fills:
        flash("Please record a fill before entering counterparties.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    # Use fill_id from the form if provided — allows allocating any pending fill,
    # not just the latest. Falls back to latest fill for backwards compatibility.
    fill_id_str = request.form.get("fill_id", "").strip()
    if fill_id_str:
        fill = Fill.query.filter_by(
            id=int(fill_id_str), order_id=order.id
        ).first_or_404()
    else:
        fill = order.fills[-1]

    # Block if prices not entered for this specific fill
    if not fill.leg_prices or len(fill.leg_prices) == 0:
        flash("Please save leg prices before entering counterparties.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))
    try:
        house = request.form.get("house", "").strip()
        account = request.form.get("account", "").strip()
        bk_broker = request.form.get("bk_broker", "").strip()
        if house:
            order.house = house
        if account:
            order.account = account
        order.bk_broker = bk_broker if bk_broker else order.bk_broker

        counterparties = []
        for i in range(20):
            qty_str = request.form.get(f"cp_qty_{i}", "").strip()
            broker = request.form.get(f"cp_broker_{i}", "").strip()
            symbol = request.form.get(f"cp_symbol_{i}", "").strip()
            cp_house = request.form.get(f"cp_house_{i}", "").strip()
            bracket = request.form.get(f"cp_bracket_{i}", "").strip()
            notes = request.form.get(f"cp_notes_{i}", "").strip()
            if not any([qty_str, broker, symbol, bracket]):
                continue
            fut_qty_str = request.form.get(f"cp_fut_qty_{i}", "").strip()
            fut_qty = int(fut_qty_str) if fut_qty_str else None
            cp = FillCounterparty(
                fill_id=fill.id,
                quantity=int(qty_str) if qty_str else 0,
                broker=broker,
                symbol=symbol,
                cp_house=cp_house or None,
                bracket=bracket,
                notes=notes or None,
                futures_quantity=fut_qty,
            )
            counterparties.append(cp)

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


@orders_bp.route("/<int:order_id>/fills/<int:fill_id>/amend", methods=["POST"])
@login_required
def amend_fill(order_id, fill_id):
    """
    Combined amendment: update prices AND counterparties for an allocated fill
    in a single transaction.  Either section may be omitted — if no price fields
    are submitted the price records are left untouched; same for counterparties.
    Logs both changes to the audit trail and stamps the order AMENDED.
    """
    order = _get_order_or_404(order_id)
    fill = Fill.query.filter_by(id=fill_id, order_id=order.id).first_or_404()

    if fill.allocation_status != AllocationStatus.ALLOCATED:
        flash("Only allocated fills can be amended.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    try:
        # ── Prices ──
        price_strs = {
            leg.leg_index: request.form.get(f"price_{leg.leg_index}", "").strip()
            for leg in order.legs
        }
        has_prices = any(v for v in price_strs.values())

        if has_prices:
            before_prices = {lp.leg_index: lp.price for lp in fill.leg_prices}
            leg_prices = []
            for leg in order.legs:
                pstr = price_strs.get(leg.leg_index, "")
                if pstr:
                    leg_prices.append(FillLegPrice(
                        fill_id=fill.id,
                        leg_index=leg.leg_index,
                        price=float(pstr),
                    ))
            if order.is_generic:
                price_override = request.form.get("price_override_confirmed") == "1"
                discrepancy = _validate_generic_prices(order, leg_prices, override=price_override)
                if price_override and discrepancy > 0.000001:
                    audit_service.log_action(
                        action="price_reconciliation_overridden",
                        entity_type="fill",
                        entity_id=fill.id,
                        tenant_id=current_user.tenant_id,
                        user_id=current_user.id,
                        notes=(
                            f"Order #{order.ticket_display}: trader confirmed odd-lot "
                            f"pricing override during amendment. Discrepancy: "
                            f"{discrepancy:.4f} (expected {order.package_premium:.4f})."
                        ),
                    )
            else:
                validate_fill_prices(order, fill, leg_prices)

            FillLegPrice.query.filter_by(fill_id=fill.id).delete()
            for lp in leg_prices:
                db.session.add(lp)

            price_map = {lp.leg_index: lp.price for lp in leg_prices}
            for leg in order.legs:
                if leg.leg_index in price_map:
                    leg.price = price_map[leg.leg_index]

            after_prices = {lp.leg_index: lp.price for lp in leg_prices}
            audit_service.log_fill_price_amended(
                fill, current_user.tenant_id, before_prices, after_prices,
            )

        # ── House / Account / BK Broker ──
        house = request.form.get("house", "").strip()
        account = request.form.get("account", "").strip()
        bk_broker = request.form.get("bk_broker", "").strip()
        if house:
            order.house = house
        if account:
            order.account = account
        if bk_broker:
            order.bk_broker = bk_broker

        # ── Counterparties ──
        before_cp = [
            {"qty": cp.quantity, "broker": cp.broker,
             "symbol": cp.symbol, "bracket": cp.bracket, "notes": cp.notes}
            for cp in fill.counterparties
        ]
        counterparties = []
        for i in range(20):
            qty_str = request.form.get(f"cp_qty_{i}", "").strip()
            broker  = request.form.get(f"cp_broker_{i}", "").strip()
            symbol  = request.form.get(f"cp_symbol_{i}", "").strip()
            cp_house = request.form.get(f"cp_house_{i}", "").strip()
            bracket = request.form.get(f"cp_bracket_{i}", "").strip()
            notes   = request.form.get(f"cp_notes_{i}", "").strip()
            if not any([qty_str, broker, symbol, bracket]):
                continue
            fut_qty_str = request.form.get(f"cp_fut_qty_{i}", "").strip()
            fut_qty = int(fut_qty_str) if fut_qty_str else None
            counterparties.append(FillCounterparty(
                fill_id=fill.id,
                quantity=int(qty_str) if qty_str else 0,
                broker=broker, symbol=symbol,
                cp_house=cp_house or None,
                bracket=bracket,
                notes=notes or None,
                futures_quantity=fut_qty,
            ))

        if counterparties:
            validate_counterparty_completeness(counterparties)
            validate_counterparty_quantities(fill, counterparties)
            FillCounterparty.query.filter_by(fill_id=fill.id).delete()
            for cp in counterparties:
                db.session.add(cp)
            after_cp = [
                {"qty": cp.quantity, "broker": cp.broker,
                 "symbol": cp.symbol, "bracket": cp.bracket, "notes": cp.notes}
                for cp in counterparties
            ]
            from app.models.audit import AuditAction
            audit_service.log_action(
                action=AuditAction.COUNTERPARTY_MODIFIED,
                entity_type="fill", entity_id=fill.id,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                before_value={"counterparties": before_cp},
                after_value={"counterparties": after_cp},
                notes=f"Fill #{fill.id} amended on order #{order.ticket_display}.",
            )

        # The order stays in its current filled state — the amendment is a
        # paperwork correction (prices/counterparties) not a change to what
        # was traded. The audit log records the full before/after detail.

        db.session.commit()
        flash("Amendment saved.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(f"Validation failed: {'; '.join(e.errors)}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving amendment: {e}", "danger")
    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/fills/<int:fill_id>/amend-counterparties", methods=["POST"])
@login_required
def amend_fill_counterparties(order_id, fill_id):
    """
    Amend the counterparty allocation for an already-allocated fill.
    Replaces existing FillCounterparty records and re-validates quantities.
    Logs to audit trail as COUNTERPARTY_MODIFIED.
    Available on any allocated fill regardless of order status.
    """
    order = _get_order_or_404(order_id)
    fill = Fill.query.filter_by(id=fill_id, order_id=order.id).first_or_404()

    if fill.allocation_status != AllocationStatus.ALLOCATED:
        flash("Only allocated fills can be amended.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    try:
        # Capture before-state for audit
        before = [
            {"qty": cp.quantity, "broker": cp.broker,
             "symbol": cp.symbol, "bracket": cp.bracket, "notes": cp.notes}
            for cp in fill.counterparties
        ]

        house = request.form.get("house", "").strip()
        account = request.form.get("account", "").strip()
        bk_broker = request.form.get("bk_broker", "").strip()
        if house:
            order.house = house
        if account:
            order.account = account
        if bk_broker:
            order.bk_broker = bk_broker

        counterparties = []
        for i in range(20):
            qty_str = request.form.get(f"cp_qty_{i}", "").strip()
            broker = request.form.get(f"cp_broker_{i}", "").strip()
            symbol = request.form.get(f"cp_symbol_{i}", "").strip()
            cp_house = request.form.get(f"cp_house_{i}", "").strip()
            bracket = request.form.get(f"cp_bracket_{i}", "").strip()
            notes = request.form.get(f"cp_notes_{i}", "").strip()
            if not any([qty_str, broker, symbol, bracket]):
                continue
            fut_qty_str = request.form.get(f"cp_fut_qty_{i}", "").strip()
            fut_qty = int(fut_qty_str) if fut_qty_str else None
            cp = FillCounterparty(
                fill_id=fill.id,
                quantity=int(qty_str) if qty_str else 0,
                broker=broker,
                symbol=symbol,
                cp_house=cp_house or None,
                bracket=bracket,
                notes=notes or None,
                futures_quantity=fut_qty,
            )
            counterparties.append(cp)

        validate_counterparty_completeness(counterparties)
        validate_counterparty_quantities(fill, counterparties)

        FillCounterparty.query.filter_by(fill_id=fill.id).delete()
        for cp in counterparties:
            db.session.add(cp)

        after = [
            {"qty": cp.quantity, "broker": cp.broker,
             "symbol": cp.symbol, "bracket": cp.bracket, "notes": cp.notes}
            for cp in counterparties
        ]

        from app.models.audit import AuditAction
        audit_service.log_action(
            action=AuditAction.COUNTERPARTY_MODIFIED,
            entity_type="fill",
            entity_id=fill.id,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            before_value={"counterparties": before},
            after_value={"counterparties": after},
            notes=f"Counterparties amended for fill #{fill.id} on order #{order.ticket_display}.",
        )
        db.session.commit()
        flash("Counterparties amended.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(f"Validation failed: {'; '.join(e.errors)}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error amending counterparties: {e}", "danger")
    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/fills/<int:fill_id>/amend-prices", methods=["POST"])
@login_required
def amend_fill_prices(order_id, fill_id):
    """
    Amend the leg prices for an already-priced fill.
    Re-runs price reconciliation validation (hard block).
    Stamps order as AMENDED if currently in a terminal filled state.
    Logs to audit trail as FILL_PRICE_AMENDED.
    """
    order = _get_order_or_404(order_id)
    fill = Fill.query.filter_by(id=fill_id, order_id=order.id).first_or_404()

    try:
        # Capture before-state
        before_prices = {lp.leg_index: lp.price for lp in fill.leg_prices}

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

        if order.is_generic:
            price_override = request.form.get("price_override_confirmed") == "1"
            discrepancy = _validate_generic_prices(order, leg_prices, override=price_override)
            if price_override and discrepancy > 0.000001:
                audit_service.log_action(
                    action="price_reconciliation_overridden",
                    entity_type="fill",
                    entity_id=fill.id,
                    tenant_id=current_user.tenant_id,
                    user_id=current_user.id,
                    notes=(
                        f"Order #{order.ticket_display}: trader confirmed odd-lot "
                        f"pricing override during price amendment. Discrepancy: "
                        f"{discrepancy:.4f} (expected {order.package_premium:.4f})."
                    ),
                )
        else:
            validate_fill_prices(order, fill, leg_prices)

        FillLegPrice.query.filter_by(fill_id=fill.id).delete()
        for lp in leg_prices:
            db.session.add(lp)

        # Update OrderLeg.price display field
        price_map = {lp.leg_index: lp.price for lp in leg_prices}
        for leg in order.legs:
            if leg.leg_index in price_map:
                leg.price = price_map[leg.leg_index]

        after_prices = {lp.leg_index: lp.price for lp in leg_prices}

        audit_service.log_fill_price_amended(
            fill, current_user.tenant_id, before_prices, after_prices,
        )

        # The order stays in its current filled state — price correction is
        # a paperwork amendment. Audit trail records the full before/after.

        db.session.commit()
        flash("Prices amended and validated.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(f"Price validation failed: {'; '.join(e.errors)}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error amending prices: {e}", "danger")
    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/cancel", methods=["POST"])
@login_required
def cancel(order_id):
    order = _get_order_or_404(order_id)
    old_status = order.status
    try:
        if order.status == OrderStatus.OPEN:
            order.transition_to(OrderStatus.CANCELLED)
        elif order.status == OrderStatus.PARTIAL_FILL:
            # Scale leg volumes to the filled quantity before transitioning.
            # transition_to(PARTIAL_CANCELLED) sets order.total_quantity = filled_quantity,
            # but leg.volume records are not touched automatically. Without this, the
            # ticket and cards would still show the original order size rather than
            # what was actually traded.
            filled = order.filled_quantity
            original = order.total_quantity
            if filled > 0 and original > 0 and filled != original:
                scale = filled / original
                for leg in order.legs:
                    leg.volume = round(leg.volume * scale)
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


@orders_bp.route("/bulk-delete", methods=["POST"])
@login_required
def bulk_delete():
    # Bulk soft-delete. Super admin only.
    if not current_user.is_super():
        flash("Only super admins can delete orders.", "danger")
        return redirect(url_for("reports.order_log"))
    from datetime import datetime, timezone as _tz
    order_ids = request.form.getlist("order_ids")
    if not order_ids:
        flash("No orders selected.", "warning")
        return redirect(url_for("reports.order_log"))
    deleted = 0
    now = datetime.now(_tz.utc)
    for oid in order_ids:
        order = Order.query.filter_by(
            id=int(oid), tenant_id=current_user.tenant_id
        ).first()
        if order and order.deleted_at is None:
            order.deleted_at = now
            audit_service.log_action(
                action="order_deleted",
                entity_type="order",
                entity_id=order.id,
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                notes=f"Order #{order.ticket_display} bulk-deleted by super admin.",
            )
            deleted += 1
    db.session.commit()
    flash(f"{deleted} order(s) deleted.", "info")
    return redirect(url_for("reports.order_log"))


@orders_bp.route("/<int:order_id>/delete", methods=["POST"])
@login_required
def delete(order_id):
    # Hard-delete an order. Super admin only, any status.
    # Physically removes the row (and cascaded fills/legs) so the ticket
    # number is freed from the unique constraint and can be reused.
    # Audit log entry is written BEFORE deletion so the record is preserved.
    if not current_user.is_super():
        flash("Only super admins can delete orders.", "danger")
        return redirect(url_for("orders.detail", order_id=order_id))

    order = _get_order_or_404(order_id)
    ticket_num = order.ticket_number
    ticket_display = order.ticket_display

    # Write audit entry before the row disappears
    audit_service.log_action(
        action="order_hard_deleted",
        entity_type="order",
        entity_id=order.id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        notes=(
            f"Order #{ticket_display} hard-deleted by super admin "
            f"(trade: {order.raw_input}). Ticket number released for reuse."
        ),
    )

    # Hard delete — cascades to legs, fills, counterparties
    db.session.delete(order)

    # Walk the counter back so the freed number is the next candidate.
    # _get_next_ticket_number's gap-fill loop handles any collision with
    # other still-active orders on the same day.
    tenant = db.session.get(Tenant, current_user.tenant_id)
    if ticket_num <= tenant.current_ticket_number:
        tenant.current_ticket_number = ticket_num - 1

    db.session.commit()
    flash(f"Order #{ticket_display} deleted. Ticket #{ticket_display} will be reused.", "info")
    return redirect(url_for("orders.index"))


@orders_bp.route("/<int:order_id>/cancel-duplicate", methods=["POST"])
@login_required
def cancel_duplicate(order_id):
    """
    Mark a filled/amended order as CANCELLED_DUPLICATE.
    Super admin only. Retains the record for compliance audit but excludes
    it from all volume totals. Used when the same trade was entered twice
    by different users and one entry needs to be voided after the fact.
    """
    if not current_user.is_super():
        flash("Only super admins can cancel orders as duplicates.", "danger")
        return redirect(url_for("orders.detail", order_id=order_id))

    order = _get_order_or_404(order_id)

    eligible = {
        OrderStatus.FILLED,
        OrderStatus.PARTIAL_FILL,
        OrderStatus.PARTIAL_CANCELLED,
        OrderStatus.AMENDED,  # for orders amended before this status change was removed
    }
    if order.status not in eligible:
        flash(
            f"Order #{order.ticket_display} cannot be marked as a duplicate "
            f"(current status: {order.status.replace('_', ' ').upper()}).",
            "warning",
        )
        return redirect(url_for("orders.detail", order_id=order_id))

    prev_status = order.status
    order.status = OrderStatus.CANCELLED_DUPLICATE

    audit_service.log_action(
        action="order_cancelled_duplicate",
        entity_type="order",
        entity_id=order.id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        notes=(
            f"Order #{order.ticket_display} marked CANCELLED DUPLICATE by super admin. "
            f"Previous status: {prev_status}. "
            f"Trade: {order.raw_input}. "
            f"Record retained for compliance audit."
        ),
    )

    db.session.commit()
    flash(
        f"Order #{order.ticket_display} marked as Cancelled Duplicate. "
        f"It will no longer appear in volume totals.",
        "warning",
    )
    return redirect(url_for("orders.detail", order_id=order_id))


@orders_bp.route("/<int:order_id>/correct-trade-string", methods=["GET"])
@login_required
def correct_trade_string_page(order_id):
    """Dedicated page for correcting the trade string on a filled order."""
    if not current_user.is_super():
        flash("Only super admins can correct a filled order's trade string.", "danger")
        return redirect(url_for("orders.detail", order_id=order_id))

    order = _get_order_or_404(order_id)

    eligible = {
        OrderStatus.FILLED,
        OrderStatus.PARTIAL_FILL,
        OrderStatus.PARTIAL_CANCELLED,
        OrderStatus.AMENDED,
    }
    if order.status not in eligible:
        flash("Trade string correction is only available for filled orders.", "warning")
        return redirect(url_for("orders.detail", order_id=order_id))

    if order.is_generic:
        flash("Generic orders do not have parsed legs to correct.", "warning")
        return redirect(url_for("orders.detail", order_id=order_id))

    return render_template("orders/correct_trade_string.html", order=order)


@orders_bp.route("/<int:order_id>/amend-trade-string", methods=["POST"])
@login_required
def amend_trade_string(order_id):
    """
    Correct the trade string and leg reference data (contract code, expiry,
    market) for a filled order where the wrong contract/expiry was entered.

    Super admin only. Strikes, volumes, sides, option types, prices, and
    counterparties are UNCHANGED — they are confirmed by the existing fills.
    Only reference fields that identify the contract (expiry, contract_type,
    market, mo_card_code) are updated on each leg.

    A written reason is required and written to the audit log alongside the
    old and new trade strings.
    """
    if not current_user.is_super():
        flash("Only super admins can correct a filled order's trade string.", "danger")
        return redirect(url_for("orders.detail", order_id=order_id))

    order = _get_order_or_404(order_id)

    eligible = {
        OrderStatus.FILLED,
        OrderStatus.PARTIAL_FILL,
        OrderStatus.PARTIAL_CANCELLED,
        OrderStatus.AMENDED,
    }
    if order.status not in eligible:
        flash("Trade string correction is only available for filled orders.", "warning")
        return redirect(url_for("orders.detail", order_id=order_id))

    if order.is_generic:
        flash("Generic orders do not have parsed legs to correct.", "warning")
        return redirect(url_for("orders.detail", order_id=order_id))

    new_input = request.form.get("corrected_trade_string", "").strip()
    reason    = request.form.get("correction_reason", "").strip()

    _correct_page = url_for("orders.correct_trade_string_page", order_id=order_id)

    if not new_input:
        flash("Please enter the corrected trade string.", "warning")
        return redirect(_correct_page)
    if not reason:
        flash("A reason for the correction is required.", "warning")
        return redirect(_correct_page)

    try:
        trade_parts = parse_trade_input(new_input)
        new_all_legs = []
        for part in trade_parts:
            new_all_legs.extend(build_legs(part))

        old_legs = sorted(order.legs, key=lambda l: l.leg_index)

        # Guard: same number of legs
        if len(new_all_legs) != len(old_legs):
            flash(
                f"Corrected trade string has {len(new_all_legs)} leg(s) but the "
                f"original has {len(old_legs)}. Only the contract/expiry reference "
                f"can change — the trade structure must be identical.",
                "danger",
            )
            return redirect(_correct_page)

        # Guard: same option types, strikes, and sides on each leg
        mismatches = []
        for i, (new_leg, old_leg) in enumerate(zip(new_all_legs, old_legs), 1):
            if new_leg.get("option_type") != old_leg.option_type:
                mismatches.append(
                    f"Leg {i}: option type changed "
                    f"({old_leg.option_type} → {new_leg.get('option_type')})"
                )
            if new_leg.get("strike") != old_leg.strike:
                mismatches.append(
                    f"Leg {i}: strike changed "
                    f"({old_leg.strike} → {new_leg.get('strike')})"
                )
            if new_leg.get("side") != old_leg.side:
                mismatches.append(
                    f"Leg {i}: side changed "
                    f"({old_leg.side} → {new_leg.get('side')})"
                )

        if mismatches:
            flash(
                "Corrected trade string changes the trade structure — only the "
                "contract/expiry reference should differ. Issues: "
                + "; ".join(mismatches),
                "danger",
            )
            return redirect(_correct_page)

        # All validation passed — update only reference fields
        old_raw_input = order.raw_input

        for new_leg_data, old_leg in zip(new_all_legs, old_legs):
            old_leg.contract_type = new_leg_data.get("contract_type", old_leg.contract_type)
            old_leg.expiry        = new_leg_data.get("expiry",        old_leg.expiry)
            old_leg.market        = new_leg_data.get("market",        old_leg.market)
            old_leg.mo_card_code  = new_leg_data.get("mo_card_code",  old_leg.mo_card_code)

        order.raw_input = new_input

        mods = list(order.modification_timestamps or [])
        mods.append(datetime.now(timezone.utc).isoformat())
        order.modification_timestamps = mods

        from app.models.audit import AuditAction
        audit_service.log_action(
            action=AuditAction.TRADE_STRING_CORRECTED,
            entity_type="order",
            entity_id=order.id,
            tenant_id=current_user.tenant_id,
            before_value={"raw_input": old_raw_input},
            after_value={"raw_input": new_input},
            notes=(
                f"Trade string corrected by super admin.\n"
                f"REASON: {reason}\n"
                f"OLD: {old_raw_input}\n"
                f"NEW: {new_input}"
            ),
        )

        db.session.commit()
        flash(
            f"Order #{order.ticket_display} trade string corrected. "
            f"Reprint cards and ticket to reflect the change.",
            "success",
        )

    except ParseError as e:
        flash(f"Parse error in corrected trade string: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error correcting trade string: {e}", "danger")

    return redirect(url_for("orders.detail", order_id=order_id))


@orders_bp.route("/<int:order_id>/modify", methods=["GET", "POST"])
@login_required
def modify(order_id):
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
        before = {"raw_input": order.raw_input, "strategy": order.strategy}
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
                    cvd_raw_volume=leg_data.get("cvd_raw_volume", None),
            )
            db.session.add(leg)
        after = {"raw_input": order.raw_input, "strategy": order.strategy}
        # Record modification timestamp
        if not order.modification_timestamps:
            order.modification_timestamps = []
        mods = list(order.modification_timestamps)
        mods.append(datetime.now(timezone.utc).isoformat())
        order.modification_timestamps = mods
        audit_service.log_order_modified(order, current_user.tenant_id, before, after)
        db.session.commit()
        flash(f"Order #{order.ticket_display} modified.", "success")
    except ParseError as e:
        flash(f"Parse error: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error modifying order: {e}", "danger")
    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/modify-balance", methods=["GET", "POST"])
@login_required
def modify_balance(order_id):
    """
    Modify the working balance of a partially-filled order.

    Closes the original order at its filled quantity (PARTIAL_CANCELLED) and
    opens a NEW order for the revised balance with the next ticket number.
    The closed order's raw_input is updated to reflect the actual filled
    quantity so it displays correctly (e.g. 4/800 not 4/2000).
    """
    order = _get_order_or_404(order_id)

    if order.status != OrderStatus.PARTIAL_FILL:
        flash("Only partially-filled orders can have their balance modified.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    if request.method == "GET":
        return render_template("orders/modify_balance.html", order=order)

    new_input = request.form.get("trade_string", "").strip().upper()
    if not new_input:
        flash("Please enter a trade string for the balance.", "warning")
        return redirect(url_for("orders.modify_balance", order_id=order.id))

    try:
        trade_parts = parse_trade_input(new_input)
        all_legs = []
        for part in trade_parts:
            all_legs.extend(build_legs(part))

        new_volume    = trade_parts[0].volume
        new_premium   = trade_parts[0].premium
        new_strategy  = trade_parts[0].strategy
        new_direction = trade_parts[0].direction_side

        # ── Close the original order ──────────────────────────────────────
        old_status = order.status
        filled     = order.filled_quantity
        original   = order.total_quantity

        # Update raw_input to show filled quantity (4/2000 → 4/800)
        if filled != original and original > 0:
            orig_s = str(original)
            fill_s = str(filled)
            # price/qty format:  .../ORIG → .../FILLED
            updated = re.sub(r'(?<=/)' + re.escape(orig_s) + r'',
                             fill_s, order.raw_input, count=1)
            if updated == order.raw_input:
                # qty@price format: ORIG@... → FILLED@...
                updated = re.sub(r'' + re.escape(orig_s) + r'(?=\s*@)',
                                 fill_s, order.raw_input, count=1)
            order.raw_input = updated

        # Scale leg volumes to filled quantity before closing
        if filled > 0 and original > 0 and filled != original:
            scale = filled / original
            for leg in order.legs:
                leg.volume = round(leg.volume * scale)

        order.transition_to(OrderStatus.PARTIAL_CANCELLED)

        audit_service.log_order_status_change(
            order, current_user.tenant_id, old_status, order.status,
            notes=f"Balance modified — new ticket opened for {new_volume} contracts.",
        )

        # ── Open new order for the balance ────────────────────────────────
        ticket_num = _get_next_ticket_number(current_user.tenant_id)
        new_order = Order(
            tenant_id=current_user.tenant_id,
            ticket_number=ticket_num,
            ticket_display=f"{ticket_num:04d}",
            trade_date=date.today(),
            raw_input=new_input,
            direction=new_direction,
            total_quantity=new_volume,
            package_premium=new_premium,
            strategy=new_strategy,
            is_generic=False,
            status=OrderStatus.OPEN,
            created_by_id=current_user.id,
            house=order.house,
            account=order.account,
        )
        db.session.add(new_order)
        db.session.flush()

        for idx, leg_data in enumerate(all_legs):
            leg = OrderLeg(
                order_id=new_order.id,
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
                cvd_raw_volume=leg_data.get("cvd_raw_volume"),
            )
            db.session.add(leg)

        audit_service.log_order_created(new_order, current_user.tenant_id)
        db.session.commit()

        flash(
            f"Order #{order.ticket_display} closed at {filled:,} contracts. "
            f"New order #{new_order.ticket_display} opened for {new_volume:,} contracts.",
            "success",
        )
        return redirect(url_for("orders.detail", order_id=new_order.id))

    except ParseError as e:
        flash(f"Parse error: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error modifying balance: {e}", "danger")

    return redirect(url_for("orders.modify_balance", order_id=order.id))


@orders_bp.route("/<int:order_id>/cancel-balance", methods=["GET", "POST"])
@login_required
def cancel_balance(order_id):
    """
    Cancel the remaining balance and open a NEW order (new ticket number).

    Use this for clients who want a separate ticket for the revised balance.
    """
    order = _get_order_or_404(order_id)

    if order.status != OrderStatus.PARTIAL_FILL:
        flash("Only partially-filled orders can have their balance cancelled.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    if request.method == "GET":
        return render_template("orders/cancel_balance.html", order=order)

    new_input = request.form.get("trade_string", "").strip().upper()
    if not new_input:
        flash("Please enter a trade string for the new order.", "warning")
        return redirect(url_for("orders.cancel_balance", order_id=order.id))

    try:
        trade_parts = parse_trade_input(new_input)
        all_legs = []
        for part in trade_parts:
            all_legs.extend(build_legs(part))

        new_volume    = trade_parts[0].volume
        new_premium   = trade_parts[0].premium
        new_strategy  = trade_parts[0].strategy
        new_direction = trade_parts[0].direction_side

        # Close the partial fill on the original order
        old_status = order.status
        filled     = order.filled_quantity
        original   = order.total_quantity
        if filled > 0 and original > 0 and filled != original:
            scale = filled / original
            for leg in order.legs:
                leg.volume = round(leg.volume * scale)
        order.transition_to(OrderStatus.PARTIAL_CANCELLED)

        audit_service.log_order_status_change(
            order, current_user.tenant_id, old_status, order.status,
            notes=f"Balance cancelled — new ticket will open for {new_volume} contracts.",
        )

        # Open new order for the balance
        ticket_num = _get_next_ticket_number(current_user.tenant_id)
        new_order = Order(
            tenant_id=current_user.tenant_id,
            ticket_number=ticket_num,
            ticket_display=f"{ticket_num:04d}",
            trade_date=date.today(),
            raw_input=new_input,
            direction=new_direction,
            total_quantity=new_volume,
            package_premium=new_premium,
            strategy=new_strategy,
            is_generic=False,
            status=OrderStatus.OPEN,
            created_by_id=current_user.id,
            house=order.house,
            account=order.account,
        )
        db.session.add(new_order)
        db.session.flush()

        for idx, leg_data in enumerate(all_legs):
            leg = OrderLeg(
                order_id=new_order.id,
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
                cvd_raw_volume=leg_data.get("cvd_raw_volume"),
            )
            db.session.add(leg)

        audit_service.log_order_created(new_order, current_user.tenant_id)
        db.session.commit()

        flash(
            f"Order #{order.ticket_display} closed at {order.total_quantity:,} contracts. "
            f"New order #{new_order.ticket_display} opened for {new_volume:,} contracts.",
            "success",
        )
        return redirect(url_for("orders.detail", order_id=new_order.id))

    except ParseError as e:
        flash(f"Parse error: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error cancelling balance: {e}", "danger")

    return redirect(url_for("orders.cancel_balance", order_id=order.id))
    """
    Modify the working balance of a partially-filled order.

    Workflow:
    1. Validates the order is in PARTIAL_FILL status.
    2. GET: Shows a form pre-populated with the current trade string and
       remaining quantity so the user can revise the balance.
    3. POST: Closes the partial fill (PARTIAL_CANCELLED → FILLED, total
       shrinks to filled qty) then opens a new OPEN order for the revised
       balance, copying house/account from the original.

    The original order retains all fills and counterparties. The new order
    gets the next sequential ticket number and starts clean (no fills).
    """
    order = _get_order_or_404(order_id)

    if order.status != OrderStatus.PARTIAL_FILL:
        flash("Only partially-filled orders can have their balance modified.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))

    if request.method == "GET":
        return render_template(
            "orders/modify_balance.html",
            order=order,
        )

    new_input = request.form.get("trade_string", "").strip()
    if not new_input:
        flash("Please enter a trade string for the balance.", "warning")
        return redirect(url_for("orders.modify_balance", order_id=order.id))

    try:
        # Parse the new trade string first — fail early before touching the DB
        trade_parts = parse_trade_input(new_input)
        all_legs = []
        for part in trade_parts:
            all_legs.extend(build_legs(part))

        new_direction = trade_parts[0].direction_side
        new_volume = trade_parts[0].volume
        new_premium = trade_parts[0].premium
        new_strategy = trade_parts[0].strategy

        # ── Close the partial fill on the original order ──
        old_status = order.status  # PARTIAL_FILL
        # Scale leg volumes to filled quantity before transitioning, so
        # the original order's ticket and cards show the correct amount.
        filled = order.filled_quantity
        original = order.total_quantity
        if filled > 0 and original > 0 and filled != original:
            scale = filled / original
            for leg in order.legs:
                leg.volume = round(leg.volume * scale)
        order.transition_to(OrderStatus.PARTIAL_CANCELLED)
        # transition_to(PARTIAL_CANCELLED) shrinks total_qty to filled_qty
        # and sets status to FILLED internally.

        audit_service.log_order_status_change(
            order, current_user.tenant_id, old_status, order.status,
            notes=f"Balance modified — new ticket will be opened for {new_volume} contracts.",
        )

        # ── Open new order for the balance ──
        ticket_num = _get_next_ticket_number(current_user.tenant_id)
        new_order = Order(
            tenant_id=current_user.tenant_id,
            ticket_number=ticket_num,
            ticket_display=f"{ticket_num:04d}",
            trade_date=date.today(),
            raw_input=new_input,
            direction=new_direction,
            total_quantity=new_volume,
            package_premium=new_premium,
            strategy=new_strategy,
            is_generic=False,
            status=OrderStatus.OPEN,
            created_by_id=current_user.id,
            house=order.house,
            account=order.account,
        )
        db.session.add(new_order)
        db.session.flush()

        for idx, leg_data in enumerate(all_legs):
            leg = OrderLeg(
                order_id=new_order.id,
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
                    cvd_raw_volume=leg_data.get("cvd_raw_volume", None),
            )
            db.session.add(leg)

        audit_service.log_order_created(new_order, current_user.tenant_id)
        db.session.commit()

        flash(
            f"Order #{order.ticket_display} closed at {order.total_quantity} contracts. "
            f"New order #{new_order.ticket_display} opened for {new_volume} contracts.",
            "success",
        )
        return redirect(url_for("orders.detail", order_id=new_order.id))

    except ParseError as e:
        flash(f"Parse error: {e}", "danger")
    except Exception as e:
        db.session.rollback()
        flash(f"Error modifying balance: {e}", "danger")

    return redirect(url_for("orders.modify_balance", order_id=order.id))


# =========================================================================
# Helpers
# =========================================================================

def _get_order_or_404(order_id):
    return Order.query.filter_by(
        id=order_id, tenant_id=current_user.tenant_id,
    ).first_or_404()


def _get_next_ticket_number(tenant_id):
    """
    Return the next ticket number for the tenant.

    Queries today's active ticket numbers and skips any already taken,
    filling gaps left by hard-deleted orders. Counter wraps 9999 -> 1.
    Ticket display is always zero-padded to 4 digits (0001-9999).
    """
    from datetime import date as _date
    today = _date.today()

    # Collect all ticket numbers already assigned today
    used = {
        row[0] for row in db.session.execute(
            db.select(Order.ticket_number)
            .where(Order.tenant_id == tenant_id)
            .where(Order.trade_date == today)
        ).all()
    }

    tenant = db.session.get(Tenant, tenant_id)
    candidate = (tenant.current_ticket_number or 0) + 1
    if candidate > 9999:
        candidate = 1

    # Skip any numbers already in use today (fills gaps from hard-deleted orders)
    iterations = 0
    while candidate in used:
        candidate += 1
        if candidate > 9999:
            candidate = 1
        iterations += 1
        if iterations > 9999:
            break  # Safety: should never happen in practice

    tenant.current_ticket_number = candidate
    db.session.flush()
    return candidate


def _extract_price_info(raw_input: str) -> tuple:
    """
    Extract direction, volume, and premium from a trade string.
    Works for both parsed and generic modes.
    Returns (direction, volume, premium).
    """
    # Strip trailing parenthetical, then normalise spaces around @ and /
    # so '5000 @ 2.25' and '4/500' both tokenise correctly.
    cleaned = re.sub(r'\s+\([^)]+\)\s*$', '', raw_input.strip())
    cleaned = re.sub(r'\s*@\s*', '@', cleaned)
    cleaned = re.sub(r'\s*/\s*', '/', cleaned)
    tokens = cleaned.split()

    direction = ""
    volume = 0
    premium = 0.0

    for token in tokens:
        token = token.strip()
        if "/" in token and "@" not in token:
            parts = token.split("/")
            if len(parts) == 2:
                try:
                    premium = round(float(parts[0]) * 0.01, 4)
                    volume = int(float(parts[1]))
                    direction = "B"
                except ValueError:
                    pass
        elif "@" in token:
            parts = token.split("@")
            if len(parts) == 2:
                try:
                    volume = int(float(parts[0]))
                    premium = round(float(parts[1]) * 0.01, 4)
                    direction = "S"
                except ValueError:
                    pass

    if not direction:
        direction = "B"
    if volume == 0:
        volume = 1  # Fallback for generic mode

    return direction, volume, premium


def _validate_generic_prices(order, leg_prices, override=False):
    """
    Validate generic trade prices using qty * price net calculation.
    For each leg: sign = +1 if sell, -1 if buy.
    Net = sum(sign * volume * price) for all legs.
    |Net / order.total_quantity| should equal package premium.

    We divide by order.total_quantity (not the first leg's volume) because
    the package premium is always expressed per total-order lot. A trader
    may split one side into multiple partial legs at different prices to
    represent an average (e.g. two 500-lot buy legs on a 1000-lot order),
    and those partial legs must reconcile to the same package premium as if
    entered as a single 1000-lot leg.

    Generic orders sometimes intentionally use leg volumes that don't divide
    evenly into the package's original ratio (e.g. an odd-lot put spread
    traded at 5500 against a 1833-lot call instead of an exact 3x = 5499).
    That kind of deliberate imbalance shifts the realized net premium by a
    small amount even though the trade is correct. When `override=True`,
    a discrepancy is not raised as an error — the caller (route) is
    responsible for requiring an explicit trader confirmation first and for
    logging the discrepancy amount to the audit trail.

    Returns the discrepancy (0.0 if prices reconcile exactly).
    """
    if not order.package_premium:
        return 0.0  # No premium to validate against

    if not order.total_quantity or order.total_quantity == 0:
        return 0.0

    price_map = {lp.leg_index: lp.price for lp in leg_prices}
    net = 0.0

    for leg in order.legs:
        if leg.leg_index not in price_map:
            continue
        if leg.option_type is None and leg.strike is None:
            continue  # Skip futures legs
        sign = 1.0 if leg.side == "S" else -1.0
        net += sign * leg.volume * price_map[leg.leg_index]

    net_per_unit = abs(net) / order.total_quantity
    discrepancy = abs(net_per_unit - order.package_premium)
    if discrepancy > 0.000001:
        if override:
            return discrepancy
        raise ValidationError([
            f"Price reconciliation failed. "
            f"Expected: {order.package_premium:.4f}, "
            f"Calculated: {net_per_unit:.4f}, "
            f"Discrepancy: {discrepancy:.4f}. "
            f"If this is intentional (e.g. an odd-lot leg that doesn't divide "
            f"evenly into the package ratio), check 'Confirm odd-lot pricing' "
            f"below and save again."
        ])
    return discrepancy