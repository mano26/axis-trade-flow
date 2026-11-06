#!/usr/bin/env python3
# =============================================================================
# Staging bootstrap — first super admin + demo companies for an EMPTY database
# =============================================================================
# Runs as the staging service's pre-deploy command. It does nothing unless:
#   - STAGING_BOOTSTRAP=1 is set (never set it in production), and
#   - the database has no users at all.
# So it is safe to leave in place: after the first run it is a no-op.
#
# Creates:
#   - "AXIS" company (classic design) with a super admin
#   - "Roma Ventures" company using the new ConfirmEngine design
#
# Login comes from env vars so no password is ever in the repo:
#   STAGING_ADMIN_EMAIL, STAGING_ADMIN_PASSWORD
# =============================================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models.tenant import Tenant
from app.models.user import User, UserRole


def main() -> int:
    if os.environ.get("STAGING_BOOTSTRAP") != "1":
        print("staging_bootstrap: STAGING_BOOTSTRAP is not 1 — skipping.")
        return 0

    email = (os.environ.get("STAGING_ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("STAGING_ADMIN_PASSWORD") or ""
    if not email or len(password) < 12:
        print("staging_bootstrap: STAGING_ADMIN_EMAIL / STAGING_ADMIN_PASSWORD (12+ chars) required — skipping.")
        return 0

    app = create_app()
    with app.app_context():
        if User.query.execution_options(skip_tenant_guard=True).count() > 0:
            print("staging_bootstrap: users already exist — nothing to do.")
            return 0

        home = Tenant(name="AXIS", slug="axis", ui_theme="classic")
        roma = Tenant(name="Roma Ventures", slug="roma-ventures", ui_theme="confirmengine")
        db.session.add_all([home, roma])
        db.session.flush()

        admin = User(
            tenant_id=home.id, email=email, display_name="Mike Romano",
            role=UserRole.ADMIN, is_super_admin=True,
        )
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print(f"staging_bootstrap: created companies AXIS + Roma Ventures and super admin {email}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
