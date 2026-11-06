# =============================================================================
# SOFR options listed-expiry limit
# =============================================================================
# Options are listed 24 quarterly expiries out from the current quarter.
# A contract that resolves past the last listed quarterly is rejected with a
# clear message instead of silently rolling 10 years forward. Serial months
# and mid-curves are checked against their quarterly. Futures legs are not
# limited.
# =============================================================================
from datetime import date

import pytest

from app.services import strategy_handlers as sh
from app.services.trade_parser import parse_trade_input, ParseError
from app.services.strategy_handlers import build_legs

pytestmark = pytest.mark.listed_limit


class _FrozenDate(date):
    today_value = date(2026, 11, 6)

    @classmethod
    def today(cls):
        return cls.today_value


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(sh, "date", _FrozenDate)
    _FrozenDate.today_value = date(2026, 11, 6)
    yield _FrozenDate


def legs(s):
    out = []
    for part in parse_trade_input(s):
        out.extend(build_legs(part))
    return out


def test_last_listed_matches_the_desk():
    assert sh.last_listed_quarterly(date(2026, 11, 6)) == (2032, 9)     # Sep 32


def test_last_listed_rolls_forward_each_quarter():
    assert sh.last_listed_quarterly(date(2026, 12, 15)) == (2032, 9)
    assert sh.last_listed_quarterly(date(2027, 1, 4)) == (2032, 12)
    assert sh.last_listed_quarterly(date(2027, 3, 1)) == (2032, 12)
    assert sh.last_listed_quarterly(date(2027, 4, 1)) == (2033, 3)


@pytest.mark.parametrize("trade", [
    "SFRZ6 96.25 96.50 CS 1.75/2500",     # front quarterly
    "SFRU2 96.25 96.50 CS 1/100",         # Sep 32: the last listed
    "SFRF7 96.25 C 1/100",                # serial Jan 27 -> Mar 27 quarterly
    "SFRM7 95.75 C CVD 95.52 D 37 2/100", # CVD: futures leg not limited
    "2QH7 96.25 C 1/100",                 # mid-curve off Mar 27
])
def test_listed_contracts_parse(trade):
    assert legs(trade)


@pytest.mark.parametrize("trade, expect", [
    ("SFRH6 96.00 96.25 CS 4/500", "March 2026 has expired, and March 2036 is past the last listed SOFR option expiry (Sep 2032)"),
    ("SFRZ2 96.25 C 1/100", "December 2032 is past the last listed"),
    ("SFRH4 96.25 C 1/100", "March 2034 is past the last listed"),
    ("2QH6 96.25 C 1/100", "past the last listed"),     # mid-curve off an invalid quarterly
])
def test_unlisted_contracts_are_rejected_with_a_clear_message(trade, expect):
    with pytest.raises(ParseError) as e:
        legs(trade)
    assert expect in str(e.value)


def test_limit_moves_with_the_calendar(frozen_today):
    with pytest.raises(ParseError):
        legs("SFRZ2 96.25 C 1/100")                    # Dec 32 not listed in Nov 26
    frozen_today.today_value = date(2027, 1, 4)
    assert legs("SFRZ2 96.25 C 1/100")                 # listed once Dec 26 expires
