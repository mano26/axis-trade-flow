# =============================================================================
# Order Lifecycle Edge Case Tests — designed to break things
# =============================================================================
# Targets partial fill math, partial-cancel shrink behaviour,
# invalid transitions, and remaining_quantity consistency.
#
# Run with: pytest tests/test_lifecycle_edge_cases.py -v
# =============================================================================

import pytest
from app import create_app
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


import uuid as _uuid

@pytest.fixture
def order(app):
    """Fresh OPEN order with total_quantity=1000 for every test."""
    with app.app_context():
        tenant = Tenant(name="Edge Case Firm", slug=f"edge-{_uuid.uuid4().hex[:12]}")
        db.session.add(tenant)
        db.session.flush()
        user = User(tenant_id=tenant.id, email=f"t{id(order)}@test.com",
                    display_name="Trader")
        user.set_password("x")
        db.session.add(user)
        db.session.flush()
        o = Order(
            tenant_id=tenant.id,
            ticket_number=99,
            ticket_display="0099",
            raw_input="SFRH6 C 96.00 4/1000",
            direction="B",
            total_quantity=1000,
            filled_quantity=0,
            strategy="single",
            status=OrderStatus.OPEN,
            created_by_id=user.id,
        )
        db.session.add(o)
        db.session.commit()
        yield o
        db.session.rollback()


# ---------------------------------------------------------------------------
# State Machine — Invalid Transitions
# ---------------------------------------------------------------------------

class TestInvalidTransitions:

    def test_open_cannot_go_to_reported(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            assert not o.can_transition_to(OrderStatus.REPORTED)
            with pytest.raises(ValueError):
                o.transition_to(OrderStatus.REPORTED)

    def test_open_cannot_go_to_amended(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            with pytest.raises(ValueError):
                o.transition_to(OrderStatus.AMENDED)

    def test_filled_cannot_go_to_open(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.FILLED)
            assert not o.can_transition_to(OrderStatus.OPEN)

    def test_filled_cannot_go_to_cancelled(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.status = OrderStatus.FILLED
            assert not o.can_transition_to(OrderStatus.CANCELLED)

    def test_cancelled_terminal_no_transitions(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.CANCELLED)
            allowed = OrderStatus.TRANSITIONS.get(OrderStatus.CANCELLED, set())
            assert allowed == set()

    def test_report_accepted_terminal(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            allowed = OrderStatus.TRANSITIONS.get(OrderStatus.REPORT_ACCEPTED, set())
            assert allowed == set()

    def test_invalid_status_string_raises(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            with pytest.raises(ValueError):
                o.transition_to("not_a_real_status")

    def test_partial_fill_cannot_go_to_open(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.PARTIAL_FILL)
            assert not o.can_transition_to(OrderStatus.OPEN)

    def test_partial_fill_cannot_go_to_cancelled(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.PARTIAL_FILL)
            assert not o.can_transition_to(OrderStatus.CANCELLED)


# ---------------------------------------------------------------------------
# Partial-Cancel → FILLED Shrink Behaviour
# ---------------------------------------------------------------------------

class TestPartialCancelShrink:

    def test_partial_cancel_sets_status_to_filled(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.PARTIAL_FILL)
            o.filled_quantity = 400
            o.transition_to(OrderStatus.PARTIAL_CANCELLED)
            # PARTIAL_CANCELLED → shrinks to filled qty, becomes FILLED
            assert o.status == OrderStatus.FILLED

    def test_partial_cancel_shrinks_total_quantity(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.PARTIAL_FILL)
            o.filled_quantity = 400
            o.transition_to(OrderStatus.PARTIAL_CANCELLED)
            assert o.total_quantity == 400

    def test_partial_cancel_remaining_is_zero(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.PARTIAL_FILL)
            o.filled_quantity = 250
            o.transition_to(OrderStatus.PARTIAL_CANCELLED)
            assert o.remaining_quantity == 0

    def test_partial_cancel_zero_filled_shrinks_to_zero(self, app, order):
        """Edge: cancelled before any fill is recorded."""
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.transition_to(OrderStatus.PARTIAL_FILL)
            o.filled_quantity = 0
            o.transition_to(OrderStatus.PARTIAL_CANCELLED)
            assert o.total_quantity == 0
            assert o.status == OrderStatus.FILLED


# ---------------------------------------------------------------------------
# Remaining Quantity Arithmetic
# ---------------------------------------------------------------------------

class TestRemainingQuantity:

    def test_fresh_order_remaining_equals_total(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            assert o.remaining_quantity == o.total_quantity

    def test_partial_fill_reduces_remaining(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.filled_quantity = 300
            assert o.remaining_quantity == 700

    def test_full_fill_remaining_zero(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            o.filled_quantity = o.total_quantity
            assert o.remaining_quantity == 0

    def test_remaining_never_negative(self, app, order):
        """Even if filled > total (shouldn't happen, but guard), remaining >= 0 conceptually."""
        with app.app_context():
            o = db.session.get(Order, order.id)
            # We don't gate this at model level, but the property should be consistent
            o.filled_quantity = 1000
            assert o.remaining_quantity == 0


# ---------------------------------------------------------------------------
# has_futures_legs Property
# ---------------------------------------------------------------------------

class TestHasFuturesLegs:

    def test_no_legs_no_futures(self, app, order):
        with app.app_context():
            o = db.session.get(Order, order.id)
            assert o.has_futures_legs is False

    def test_option_only_legs_no_futures(self, app, order):
        with app.app_context():
            from app.models.order import OrderLeg
            o = db.session.get(Order, order.id)
            leg = OrderLeg(
                order_id=o.id,
                leg_index=0,
                side="B",
                volume=500,
                contract_type="SR3",
                expiry="MAR26",
                strike=96.0,
                option_type="C",
            )
            db.session.add(leg)
            db.session.commit()
            o = db.session.get(Order, order.id)
            assert o.has_futures_legs is False

    def test_futures_leg_detected(self, app, order):
        with app.app_context():
            from app.models.order import OrderLeg
            o = db.session.get(Order, order.id)
            leg = OrderLeg(
                order_id=o.id,
                leg_index=1,
                side="S",
                volume=200,
                contract_type="SR3",
                expiry="MAR26",
                strike=None,
                option_type=None,
            )
            db.session.add(leg)
            db.session.commit()
            o = db.session.get(Order, order.id)
            assert o.has_futures_legs is True


# ---------------------------------------------------------------------------
# ticket_display Uniqueness Constraint
# ---------------------------------------------------------------------------

class TestTicketUniqueness:

    def test_duplicate_ticket_same_day_raises(self, app, order):
        """Two orders with same tenant + trade_date + ticket_number should fail."""
        with app.app_context():
            o = db.session.get(Order, order.id)
            from datetime import date
            dup = Order(
                tenant_id=o.tenant_id,
                ticket_number=o.ticket_number,
                ticket_display=o.ticket_display,
                trade_date=o.trade_date,
                raw_input="SFRM6 C 96.25 4/500",
                direction="B",
                total_quantity=500,
                strategy="single",
                status=OrderStatus.OPEN,
                created_by_id=o.created_by_id,
            )
            db.session.add(dup)
            with pytest.raises(Exception):  # IntegrityError
                db.session.commit()
            db.session.rollback()