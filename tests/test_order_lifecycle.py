# =============================================================================
# Order Lifecycle Tests
# =============================================================================
# Integration tests for the order state machine transitions.
# Uses the testing config with an in-memory SQLite database.
# =============================================================================

import pytest
from app import create_app
from app.extensions import db
from app.models.order import Order, OrderStatus
from app.models.tenant import Tenant
from app.models.user import User


@pytest.fixture
def app():
    """Create a test application instance."""
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def setup_data(app):
    """Create test tenant, user, and order."""
    with app.app_context():
        tenant = Tenant(name="Test Firm", slug="test-firm")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            tenant_id=tenant.id,
            email="trader@test.com",
            display_name="Test Trader",
        )
        user.set_password("testpass")
        db.session.add(user)
        db.session.flush()

        order = Order(
            tenant_id=tenant.id,
            ticket_number=1,
            ticket_display="0001",
            raw_input="SFRH6 C 96.00 4/500",
            direction="B",
            total_quantity=500,
            strategy="single",
            status=OrderStatus.OPEN,
            created_by_id=user.id,
        )
        db.session.add(order)
        db.session.commit()

        yield {"tenant": tenant, "user": user, "order": order}


class TestOrderStateMachine:
    """Test valid and invalid state transitions."""

    def test_open_to_cancelled(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            assert order.can_transition_to(OrderStatus.CANCELLED)
            order.transition_to(OrderStatus.CANCELLED)
            assert order.status == OrderStatus.CANCELLED

    def test_open_to_partial_fill(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            assert order.can_transition_to(OrderStatus.PARTIAL_FILL)
            order.transition_to(OrderStatus.PARTIAL_FILL)
            assert order.status == OrderStatus.PARTIAL_FILL

    def test_open_to_filled(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            order.transition_to(OrderStatus.FILLED)
            assert order.status == OrderStatus.FILLED

    def test_cancelled_is_terminal(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            order.transition_to(OrderStatus.CANCELLED)
            assert not order.can_transition_to(OrderStatus.OPEN)
            assert not order.can_transition_to(OrderStatus.FILLED)
            with pytest.raises(ValueError):
                order.transition_to(OrderStatus.FILLED)

    def test_filled_to_reported(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            order.transition_to(OrderStatus.FILLED)
            order.transition_to(OrderStatus.REPORTED)
            assert order.status == OrderStatus.REPORTED

    def test_reported_to_accepted(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            order.transition_to(OrderStatus.FILLED)
            order.transition_to(OrderStatus.REPORTED)
            order.transition_to(OrderStatus.REPORT_ACCEPTED)
            assert order.status == OrderStatus.REPORT_ACCEPTED

    def test_report_failed_can_retry(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            order.transition_to(OrderStatus.FILLED)
            order.transition_to(OrderStatus.REPORTED)
            order.transition_to(OrderStatus.REPORT_FAILED)
            # Can retry
            assert order.can_transition_to(OrderStatus.REPORTED)
            order.transition_to(OrderStatus.REPORTED)
            assert order.status == OrderStatus.REPORTED

    def test_partial_fill_accumulation(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            order.transition_to(OrderStatus.PARTIAL_FILL)
            # Can receive additional partial fills
            assert order.can_transition_to(OrderStatus.PARTIAL_FILL)
            # Can be fully filled
            assert order.can_transition_to(OrderStatus.FILLED)

    def test_remaining_quantity(self, app, setup_data):
        with app.app_context():
            order = db.session.get(Order, setup_data["order"].id)
            assert order.remaining_quantity == 500
            order.filled_quantity = 200
            assert order.remaining_quantity == 300
