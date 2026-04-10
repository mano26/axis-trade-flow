# =============================================================================
# WSGI Entrypoint
# =============================================================================
# This file is the entrypoint for gunicorn in production (Railway) and for
# local development via `flask run`. It creates the Flask application instance
# using the app factory pattern.
# =============================================================================

from app import create_app

app = create_app()
