"""The user guide's example trade strings must parse as written, under the
real listed-expiry limit, so a trader can copy one and use it."""
import html
import re
from datetime import date
from pathlib import Path

import pytest

from app.services import strategy_handlers as sh
from app.services.trade_parser import ParseError, parse_trade_input

GUIDE = Path(__file__).resolve().parents[1] / "app" / "templates" / "guide.html"


def _examples():
    s = GUIDE.read_text()
    cands = []
    for blk in re.findall(r'class="code-block">(.*?)</div>', s, re.S) + \
            re.findall(r"<pre[^>]*>(.*?)</pre>", s, re.S):
        cands += blk.split("\n")
    cands += re.findall(r'<(?:span class="code"|code)>(.*?)</(?:span|code)>', s)
    out = []
    for c in cands:
        c = html.unescape(c).split("<-")[0].strip()
        if re.match(r"^(\[|SFR|SR3|0Q|2Q|3Q|S0|S2|S3)", c) and re.search(r"\d/\d|\d@\d", c):
            out.append(c)
    return out


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return date(2026, 11, 6)


@pytest.mark.listed_limit
def test_guide_examples_parse(monkeypatch):
    # Frozen to when the examples were last refreshed (Nov 2026); refresh the
    # guide's contract months when you move this date forward.
    monkeypatch.setattr(sh, "date", _FrozenDate)
    examples = _examples()
    assert len(examples) > 30
    for ex in examples:
        for part in parse_trade_input(ex):
            sh.build_legs(part)


def test_guide_has_sofr_code_reference():
    s = GUIDE.read_text()
    assert 'id="sofr-codes"' in s and 'href="#sofr-codes"' in s
    for code in ("SFRA &lt;Comdty&gt; OMON", "0QA &lt;Comdty&gt; OMON", "SR1-SR3", "UZOA"):
        assert code in s
    assert "Eris" not in s  # only the SOFR product group


def test_missing_strike_is_a_clear_error():
    with pytest.raises(ParseError, match="Not enough strikes for CS"):
        for part in parse_trade_input("SFRZ6 96.25 CS 4/500"):
            sh.build_legs(part)
