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

    # Calculate proportional display volumes based on filled qty
    display_legs = []
    for leg in order.legs:
        d = {
            "leg_index": leg.leg_index,
            "side": leg.side,
            "volume": leg.volume,
            "market": leg.market,
            "contract_type": leg.contract_type,
            "expiry": leg.expiry,
            "strike": leg.strike,
            "option_type": leg.option_type,
            "price": leg.price,
        }
        # If there are fills, show proportional volumes
        if order.filled_quantity > 0 and order.filled_quantity < order.total_quantity:
            ratio = order.filled_quantity / order.total_quantity
            d["display_volume"] = round(leg.volume * ratio)
        elif order.filled_quantity > 0 and order.filled_quantity == order.total_quantity:
            d["display_volume"] = leg.volume
        else:
            d["display_volume"] = leg.volume
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

    return render_template(
        "orders/detail.html",
        order=order,
        latest_fill=latest_fill,
        all_fills_display=all_fills_display,
        lookups=lookups,
        display_legs=display_legs,
        has_prices=has_prices,
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
    Works for both parsed and generic orders.
    For generic orders, this creates/updates legs from the manual entry grid.
    For parsed orders, this updates existing leg fields (B/S, volume, strike, etc.)
    """
    order = _get_order_or_404(order_id)
    try:
        # Capture existing prices from OrderLeg before deleting.
        # Key by leg_index so we can restore after rebuild.
        existing_prices = {leg.leg_index: leg.price for leg in order.legs if leg.price is not None}

        # Also capture the FillLegPrice records keyed by leg_index so that
        # price reconciliation records survive a Save Legs operation.
        fill_price_map: dict[int, list] = {}  # leg_index → list of (fill_id, price)
        for fill in order.fills:
            for flp in fill.leg_prices:
                fill_price_map.setdefault(flp.leg_index, []).append(
                    (flp.fill_id, flp.price)
                )

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

            # Preserve existing price (e.g. CVD futures price from parser)
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

        db.session.flush()

        # Restore FillLegPrice records onto the new leg indices.
        # The new legs occupy indices 0..leg_index-1 in the same positional
        # order as before, so the old fill_price_map keys still correspond.
        for old_idx, fill_price_entries in fill_price_map.items():
            if old_idx < leg_index:  # leg still exists at this index
                for fill_id, price in fill_price_entries:
                    existing_flp = FillLegPrice.query.filter_by(
                        fill_id=fill_id, leg_index=old_idx
                    ).first()
                    if existing_flp:
                        existing_flp.price = price
                    else:
                        db.session.add(FillLegPrice(
                            fill_id=fill_id,
                            leg_index=old_idx,
                            price=price,
                        ))

        # For generic orders, set total_quantity from first leg volume
        if order.is_generic and leg_index > 0:
            first_leg = OrderLeg.query.filter_by(
                order_id=order.id, leg_index=0
            ).first()
            if first_leg:
                order.total_quantity = first_leg.volume

        db.session.commit()
        flash(f"Legs saved — {leg_index} leg(s).", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error saving legs: {e}", "danger")
    return redirect(url_for("orders.detail", order_id=order.id))


@orders_bp.route("/<int:order_id>/save-prices", methods=["POST"])
@login_required
def save_prices(order_id):
    order = _get_order_or_404(order_id)
    if not order.fills:
        flash("Please record a fill before entering prices.", "warning")
        return redirect(url_for("orders.detail", order_id=order.id))
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
            _validate_generic_prices(order, leg_prices)
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
    fill = order.fills[-1]

    # Block if prices not entered
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
            flash("No counterparties entered.", "info")
            db.session.commit()
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
def cancel(order_id):
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


def _extract_price_info(raw_input: str) -> tuple:
    """
    Extract direction, volume, and premium from a trade string.
    Works for both parsed and generic modes.
    Returns (direction, volume, premium).
    """
    # Strip trailing parenthetical
    cleaned = re.sub(r'\s+\([^)]+\)\s*$', '', raw_input.strip())
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


def _validate_generic_prices(order, leg_prices):
    """
    Validate generic trade prices using qty * price net calculation.
    For each leg: sign = +1 if sell, -1 if buy.
    Net = sum(sign * volume * price) for all legs.
    |Net / base_volume| should equal package premium.
    """
    if not order.package_premium:
        return  # No premium to validate against

    price_map = {lp.leg_index: lp.price for lp in leg_prices}
    net = 0.0
    base_vol = None

    for leg in order.legs:
        if leg.leg_index not in price_map:
            continue
        if leg.option_type is None and leg.strike is None:
            continue  # Skip futures legs
        if base_vol is None:
            base_vol = leg.volume
        sign = 1.0 if leg.side == "S" else -1.0
        net += sign * leg.volume * price_map[leg.leg_index]

    if base_vol and base_vol > 0:
        net_per_unit = abs(net) / base_vol
        if abs(net_per_unit - order.package_premium) > 0.000001:
            raise ValidationError([
                f"Price reconciliation failed. "
                f"Expected: {order.package_premium:.4f}, "
                f"Calculated: {net_per_unit:.4f}, "
                f"Discrepancy: {abs(net_per_unit - order.package_premium):.4f}."
            ])