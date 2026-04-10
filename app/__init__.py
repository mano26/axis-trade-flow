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
    # Trust proxy headers (Railway terminates SSL at the proxy)
    if app.config.get("PROXY_FIX"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # -----------------------------------------------------------------
    # Initialize extensions with the app instance
    # -----------------------------------------------------------------
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

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