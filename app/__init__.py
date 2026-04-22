# =============================================================================
# Flask Application Factory
# =============================================================================
# Creates and configures the Flask application instance. Uses the factory
# pattern so that multiple instances can be created (e.g., one for production,
# one for testing) without import-time side effects.
#
# REGULATORY NOTE: This application handles financial trade data subject to
# CME Group exchange rules and CFTC regulations. All configuration, extension
# initialization, and blueprint registration is centralized here for
# auditability.
# =============================================================================

import os
from flask import Flask
from .config import config_by_name
from .extensions import db, migrate, login_manager, csrf


def create_app(config_name: str | None = None) -> Flask:
    """
    Application factory.

    Parameters
    ----------
    config_name : str, optional
        One of "development", "production", or "testing". If not provided,
        reads from the FLASK_ENV environment variable (default: "development").

    Returns
    -------
    Flask
        Fully configured Flask application instance.
    """
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__)
    app.config.from_object(config_by_name[config_name])

    # -----------------------------------------------------------------
    # Initialize extensions with the app instance
    # -----------------------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    # -----------------------------------------------------------------
    # Jinja2 template filters
    # -----------------------------------------------------------------
    from datetime import datetime, timezone as _tz, timedelta
    try:
        from zoneinfo import ZoneInfo
        _CHICAGO = ZoneInfo("America/Chicago")
    except Exception:
        _CHICAGO = _tz(timedelta(hours=-6), "CT")  # CST fallback

    @app.template_filter("chicago_time")
    def chicago_time_filter(dt):
        """Convert a UTC datetime to Chicago time, formatted HH:MM:SS CT."""
        if dt is None:
            return ""
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            except Exception:
                return dt[:19]
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.astimezone(_CHICAGO).strftime("%H:%M:%S CT")

    # -----------------------------------------------------------------
    # Register blueprints (route modules)
    # -----------------------------------------------------------------
    _register_blueprints(app)

    # -----------------------------------------------------------------
    # Register the user loader for Flask-Login
    # -----------------------------------------------------------------
    _register_user_loader()

    # -----------------------------------------------------------------
    # Health check endpoint (used by Railway for deployment readiness)
    # -----------------------------------------------------------------
    @app.route("/health")
    def health_check():
        """
        Lightweight health check for Railway's deployment probe.
        Returns 200 if the app is running and the database is reachable.
        """
        try:
            db.session.execute(db.text("SELECT 1"))
            return {"status": "healthy"}, 200
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}, 503

    return app


def _register_blueprints(app: Flask) -> None:
    """
    Import and register all route blueprints.

    Each blueprint is a self-contained module in app/routes/ that defines
    a group of related endpoints. Blueprints are registered with a URL
    prefix to namespace their routes.
    """
    from .routes.auth import auth_bp
    from .routes.orders import orders_bp
    from .routes.fills import fills_bp
    from .routes.cards import cards_bp
    from .routes.tickets import tickets_bp
    from .routes.reports import reports_bp
    from .routes.exchange import exchange_bp
    from .routes.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(orders_bp, url_prefix="/orders")
    app.register_blueprint(fills_bp, url_prefix="/fills")
    app.register_blueprint(cards_bp, url_prefix="/cards")
    app.register_blueprint(tickets_bp, url_prefix="/tickets")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(exchange_bp, url_prefix="/exchange")
    app.register_blueprint(admin_bp, url_prefix="/admin")


def _register_user_loader() -> None:
    """
    Register the Flask-Login user loader callback.

    This function is called on every request to load the current user from
    the session. It queries the User model by primary key.
    """
    from .models.user import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))