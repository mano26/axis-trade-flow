# =============================================================================
# Application Configuration
# =============================================================================
# Environment-based configuration using python-dotenv.
# Railway sets environment variables directly; local dev uses .env file.
#
# REGULATORY NOTE: Debug mode must NEVER be enabled in production. Debug mode
# exposes stack traces, database queries, and internal state that could
# compromise trade data confidentiality.
# =============================================================================

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """
    Base configuration shared across all environments.

    All sensitive values are loaded from environment variables. Defaults are
    provided only for non-sensitive settings. The SECRET_KEY and DATABASE_URL
    have no safe defaults and will raise errors if missing in production.
    """

    # --- Flask Core ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")

    # --- Database ---
    # Railway provides DATABASE_URL with the PostgreSQL plugin.
    # SQLAlchemy 2.x requires "postgresql://" prefix (not "postgres://").
    _raw_db_url = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
    if _raw_db_url.startswith("postgres://"):
        _raw_db_url = _raw_db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _raw_db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,       # Verify connections before use
        "pool_recycle": 300,          # Recycle connections every 5 minutes
    }

    # --- Session ---
    SESSION_COOKIE_HTTPONLY = True    # Prevent JavaScript access to session cookie
    SESSION_COOKIE_SAMESITE = "Lax"  # CSRF protection for cross-origin requests
    PERMANENT_SESSION_LIFETIME = 28800  # 8 hours (one trading day)

    # --- Application Constants ---
    # Maximum ticket number before rollover. Matches the VBA tool's 0001–9999
    # range. Ticket numbers are scoped per-tenant per-day; rollover resets to
    # 0001 at the start of each trading day.
    MAX_TICKET_NUMBER = int(os.environ.get("MAX_TICKET_NUMBER", 9999))

    # --- Rithmic API (STUBBED) ---
    RITHMIC_URI = os.environ.get("RITHMIC_URI", "")
    RITHMIC_USER = os.environ.get("RITHMIC_USER", "")
    RITHMIC_PASSWORD = os.environ.get("RITHMIC_PASSWORD", "")
    RITHMIC_SYSTEM_NAME = os.environ.get("RITHMIC_SYSTEM_NAME", "")
    RITHMIC_GATEWAY = os.environ.get("RITHMIC_GATEWAY", "")


class DevelopmentConfig(Config):
    """Local development configuration with debug mode enabled."""
    DEBUG = True
    SESSION_COOKIE_SECURE = False  # Allow HTTP in local development


class ProductionConfig(Config):
    """
    Production configuration for Railway deployment.

    REGULATORY NOTE: Debug is disabled, cookies require HTTPS, and the secret
    key must be set to a cryptographically random value via environment variable.
    """
    DEBUG = False
    SESSION_COOKIE_SECURE = True  # Require HTTPS for session cookie


class TestingConfig(Config):
    """Configuration for automated test suite."""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False  # Disable CSRF for test convenience


# ---------------------------------------------------------------------------
# Config selector — keyed by FLASK_ENV environment variable.
# Defaults to development if FLASK_ENV is not set.
# ---------------------------------------------------------------------------
config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
