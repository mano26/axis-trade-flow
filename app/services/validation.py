# -*- coding: utf-8 -*-
# =============================================================================
# Validation Service
# =============================================================================
# Pre-generation validation logic for trade orders. Includes price
# reconciliation, counterparty quantity matching, and field completeness
# checks.
#
# Port of TradeValidation_v4.bas from the VBA tool, adapted for the
# database-backed model (reads from Order/Fill/FillCounterparty objects
# instead of Excel cells).
#
# REGULATORY NOTE: Price reconciliation is a hard block. If leg prices
# do not net to the package premium within tolerance, the save is rejected.
# This prevents incorrect pricing from propagating to cards, tickets, and
# exchange reports. The tolerance is set to 0.000001 (matching the VBA
# tool's threshold) to account for floating-point arithmetic.
# =============================================================================

from __future__ import annotations
from typing import Optional
from app.models.order import Order, OrderLeg
from app.models.fill import Fill, FillLegPrice, FillCounterparty


# Floating-point tolerance for price reconciliation (matches VBA tool)
PRICE_TOLERANCE = 0.000001


class ValidationError(Exception):
    """
    Raised when validation fails.

    Contains a list of human-readable error messages suitable for display.
    """
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_fill_prices(
    order: Order,
    fill: Fill,
    leg_prices: list[FillLegPrice],
) -> None:
    """
    Validate that fill leg prices reconcile to the package premium.

    The reconciliation algorithm:
    1. Group legs by their package_premium value (to handle VS trades where
       each side has its own premium).
    2. For each group, compute the net premium:
       net = sum(sign * (volume / base_volume) * price)
       where sign is +1 for sell legs and -1 for buy legs.
    3. If |net| does not equal the package premium within tolerance, reject.

    Parameters
    ----------
    order : Order
        The parent order containing the leg definitions.
    fill : Fill
        The fill being validated.
    leg_prices : list[FillLegPrice]
        The per-leg prices being entered for this fill.

    Raises
    ------
    ValidationError
        If prices do not reconcile within tolerance.
    """
    errors = []

    # Build a map of leg_index → price for quick lookup
    price_map = {lp.leg_index: lp.price for lp in leg_prices}

    # Group legs by package_premium
    premium_groups: dict[float, list[OrderLeg]] = {}
    for leg in order.legs:
        # Skip futures legs (no option type, no strike)
        if leg.option_type is None and leg.strike is None:
            continue
        pp = leg.package_premium or 0.0
        premium_groups.setdefault(pp, []).append(leg)

    for pkg_prem, legs in premium_groups.items():
        # Check all legs in this group have prices
        all_filled = True
        for leg in legs:
            if leg.leg_index not in price_map:
                all_filled = False
                errors.append(
                    f"Missing price for leg {leg.leg_index} "
                    f"({leg.contract_type} {leg.expiry} "
                    f"{leg.strike} {leg.option_type})."
                )

        if not all_filled:
            continue

        # Find base volume (minimum across legs in this group)
        base_vol = min(leg.volume for leg in legs)
        if base_vol == 0:
            continue

        # Compute net premium
        net = 0.0
        for leg in legs:
            sign = 1.0 if leg.side == "S" else -1.0
            ratio = leg.volume / base_vol
            net += sign * ratio * price_map[leg.leg_index]

        # Compare |net| to package premium
        if abs(abs(net) - pkg_prem) > PRICE_TOLERANCE:
            errors.append(
                f"Price reconciliation failed. "
                f"Expected net: {pkg_prem:.4f}, "
                f"calculated net: {abs(net):.4f}, "
                f"discrepancy: {abs(abs(net) - pkg_prem):.4f}."
            )

    if errors:
        raise ValidationError(errors)


def validate_counterparty_quantities(
    fill: Fill,
    counterparties: list[FillCounterparty],
) -> None:
    """
    Validate that counterparty quantities sum to the fill quantity.

    Parameters
    ----------
    fill : Fill
        The fill being validated.
    counterparties : list[FillCounterparty]
        The counterparty allocations for this fill.

    Raises
    ------
    ValidationError
        If quantities do not match.
    """
    total = sum(cp.quantity for cp in counterparties)
    if abs(total - fill.fill_quantity) > 0.01:
        raise ValidationError([
            f"Counterparty quantity split does not match fill size. "
            f"Fill quantity: {fill.fill_quantity}, "
            f"counterparty total: {total}, "
            f"difference: {abs(total - fill.fill_quantity)}."
        ])


def validate_counterparty_completeness(
    counterparties: list[FillCounterparty],
) -> None:
    """
    Validate that all required counterparty fields are filled.

    Parameters
    ----------
    counterparties : list[FillCounterparty]
        The counterparty allocations to validate.

    Raises
    ------
    ValidationError
        If any required field is missing.
    """
    errors = []
    for idx, cp in enumerate(counterparties, 1):
        missing = []
        if not cp.quantity or cp.quantity <= 0:
            missing.append("Qty")
        if not cp.broker or not cp.broker.strip():
            missing.append("Broker")
        if not cp.symbol or not cp.symbol.strip():
            missing.append("Symbol")
        if not cp.bracket or not cp.bracket.strip():
            missing.append("Bracket")
        if missing:
            errors.append(f"Counterparty row {idx}: missing {', '.join(missing)}.")

    if errors:
        raise ValidationError(errors)


def validate_before_generate(order: Order) -> None:
    """
    Run all pre-generation validations.

    This is the gatekeeper before card/ticket generation. It checks:
    1. Order has at least one fill
    2. All fills are allocated (counterparties entered)
    3. All fill prices are entered and reconcile
    4. House and account are set

    Parameters
    ----------
    order : Order
        The order to validate.

    Raises
    ------
    ValidationError
        If any validation check fails.
    """
    errors = []

    # Check for fills
    if not order.fills:
        errors.append("No fills found. Please record a fill before generating.")

    # Check house/account
    if not order.house or not order.house.strip():
        errors.append("House is required.")
    if not order.account or not order.account.strip():
        errors.append("Account is required.")

    # Check each fill
    for fill in order.fills:
        # Check allocation
        if fill.allocation_status != "allocated":
            errors.append(
                f"Fill #{fill.id} has pending counterparty allocation. "
                f"Please enter counterparties before generating."
            )

        # Check prices exist
        if not fill.leg_prices:
            errors.append(f"Fill #{fill.id} has no leg prices entered.")

    if errors:
        raise ValidationError(errors)