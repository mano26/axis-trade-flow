#!/usr/bin/env python3
# =============================================================================
# Development Seed Data
# =============================================================================
# Creates a test tenant and admin user for local development.
#
# Usage:
#   flask shell < scripts/seed_dev_data.py
#   -- or --
#   python scripts/seed_dev_data.py
# =============================================================================

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.tenant import Tenant
from app.models.user import User, UserRole


def seed():
    """Create development seed data."""
    app = create_app("development")

    with app.app_context():
        # Check if already seeded
        if Tenant.query.filter_by(slug="axis-dev").first():
            print("Seed data already exists. Skipping.")
            return

        # Create tenant
        tenant = Tenant(
            name="AXIS Development",
            slug="axis-dev",
        )
        db.session.add(tenant)
        db.session.flush()

        # Create admin user
        admin = User(
            tenant_id=tenant.id,
            email="admin@axis.dev",
            display_name="Admin User",
            role=UserRole.ADMIN,
        )
        admin.set_password("admin123")
        db.session.add(admin)

        # Create regular user
        trader = User(
            tenant_id=tenant.id,
            email="trader@axis.dev",
            display_name="Test Trader",
            role=UserRole.USER,
        )
        trader.set_password("trader123")
        db.session.add(trader)

        db.session.commit()

        print("Seed data created:")
        print(f"  Tenant: {tenant.name} ({tenant.slug})")
        print(f"  Admin:  {admin.email} / admin123")
        print(f"  Trader: {trader.email} / trader123")


if __name__ == "__main__":
    seed()
