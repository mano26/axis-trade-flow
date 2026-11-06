# =============================================================================
# Shared test setup
# =============================================================================
import pytest


@pytest.fixture(autouse=True)
def _no_listed_expiry_limit_by_default(request, monkeypatch):
    """Most parser/strategy tests hard-code contract codes (e.g. SFRH6) to test
    leg building, not listing dates, and would start failing as those months
    expire. The listed-expiry limit is switched off for them and tested on its
    own in tests/test_listed_expiry.py (marked `listed_limit`)."""
    if request.node.get_closest_marker("listed_limit"):
        yield
        return
    from app.services import strategy_handlers
    monkeypatch.setattr(strategy_handlers, "LISTED_QUARTERLY_EXPIRIES", 10_000)
    yield


def pytest_configure(config):
    config.addinivalue_line("markers", "listed_limit: keep the real SOFR listed-expiry limit")
