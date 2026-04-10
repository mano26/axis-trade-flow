# =============================================================================
# Flask Extensions
# =============================================================================
# Centralized extension instances, initialized without an app. The app factory
# in __init__.py calls init_app() on each extension during startup.
#
# This pattern avoids circular imports: models and routes import the extension
# objects from here rather than from the app factory module.
# =============================================================================

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

# --- Database ---
# SQLAlchemy instance shared across all models.
db = SQLAlchemy()

# --- Migrations ---
# Alembic integration via Flask-Migrate.
migrate = Migrate()

# --- Authentication ---
# Flask-Login manages user sessions and the @login_required decorator.
login_manager = LoginManager()
login_manager.login_view = "auth.login"          # Redirect target for anonymous users
login_manager.login_message_category = "warning"  # Flash message category

# --- CSRF Protection ---
# Enabled globally; all POST/PUT/DELETE requests require a CSRF token.
# REGULATORY NOTE: CSRF protection prevents unauthorized state changes to
# trade data via cross-site request forgery attacks.
csrf = CSRFProtect()
