# =============================================================================
# Strategy Handlers
# =============================================================================
# Converts parsed TradeInput objects into OrderLeg database records.
#
# Each strategy builder returns a list of leg dictionaries. Ratios are
# applied universally: if trade.ratios is set and matches the leg count,
# each leg's volume is base_volume * ratio[i]. If no custom ratios,
# the strategy's default ratios are used (e.g., 1:2:1 for butterfly).
#
# REGULATORY NOTE: The leg-building logic determines the side, volume,
# and structure of each leg. Changes affect trade confirmations and
# exchange reporting. Regression test against known trade strings.
# =============================================================================

from __future__ import annotations
from datetime import date
from typing import Optional
from app.services.trade_parser import TradeInput


# =========================================================================
# Contract Code Resolution
# =========================================================================

_MONTH_CODE_MAP = {
    "F": "JAN", "G": "FEB", "H": "MAR", "J": "APR",
    "K": "MAY", "M": "JUN", "N": "JUL", "Q": "AUG",
    "U": "SEP", "V": "OCT", "X": "NOV", "Z": "DEC",
}

_QUARTERLY_MAP = {
    "F": "MAR", "G": "MAR", "H": "MAR",
    "J": "JUN", "K": "JUN", "M": "JUN",
    "N": "SEP", "Q": "SEP", "U": "SEP",
    "V": "DEC", "X": "DEC", "Z": "DEC",
}

_QUARTERLY_LETTER_MAP = {
    "F": "H", "G": "H", "H": "H",
    "J": "M", "K": "M", "M": "M",
    "N": "U", "Q": "U", "U": "U",
    "V": "Z", "X": "Z", "Z": "Z",
}


def get_expiry(code: str, is_future: bool = False) -> str:
    code = code.upper().strip()
    suffix = code[-2:]
    month_code = suffix[0]
    year_digit = int(suffix[1])
    current_year = date.today().year
    current_month = date.today().month

    # Month number lookup for expiry-past checking
    _month_num = {
        "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
        "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
    }

    year_num = 2020 + year_digit
    if year_digit < 5:
        year_num += 10

    # Check if the resolved month/year is in the past
    # If so, bump forward by 10 years (quarterlies) or 1 year (serials)
    month_num = _month_num.get(month_code, 1)

    def is_expired(y, m):
        return (y < current_year) or (y == current_year and m < current_month)

    while is_expired(year_num, month_num):
        if month_code in ("H", "M", "U", "Z"):
            year_num += 10
        else:
            year_num += 1

    if year_num > current_year + 10:
        year_num = current_year + 10

    if is_future:
        month_name = _QUARTERLY_MAP.get(month_code, "???")
        if len(code) == 4 and code[0].isdigit():
            offset = int(code[0])
            if offset == 0:
                offset = 1
            year_num += offset
            if year_num > current_year + 10:
                year_num = current_year + 10
    else:
        month_name = _MONTH_CODE_MAP.get(month_code, "???")
    return f"{month_name}{str(year_num)[-2:]}"


def get_contract_type(code: str, is_future: bool = False) -> str:
    if is_future:
        return "SR3"
    u = code.upper()
    if u[:3] in ("SR3", "SFR"):
        return "SR3"
    if len(u) == 4 and u[0].isdigit():
        return {"0": "S0", "2": "S2", "3": "S3"}.get(u[0], "ERR")
    if u[:2] in ("S0", "S2", "S3"):
        return u[:2]
    return "ERR"


def get_card_mo_code(code: str, is_future: bool = False) -> str:
    if not is_future:
        return code.upper()
    code = code.upper()
    suffix = code[-2:]
    month_code = suffix[0]
    year_digit = int(suffix[1])
    current_year = date.today().year
    qtr_letter = _QUARTERLY_LETTER_MAP.get(month_code, month_code)
    year_num = 2020 + year_digit
    if year_digit < 5:
        year_num += 10
    if year_num < current_year:
        if month_code in ("H", "M", "U", "Z"):
            year_num += 10
        else:
            year_num += 1
    if year_num > current_year + 10:
        year_num = current_year + 10
    if len(code) == 4 and code[0].isdigit():
        offset = int(code[0])
        if offset == 0:
            offset = 1
        year_num += offset
        if year_num > current_year + 10:
            year_num = current_year + 10
    return f"SFR{qtr_letter}{year_num % 10}"


# =========================================================================
# Ratio Application
# =========================================================================

# Default ratios for each strategy (when no custom ratio is specified)
_DEFAULT_RATIOS = {
    "straddle": [1, 1],
    "strangle": [1, 1],
    "cs": [1, 1],
    "ps": [1, 1],
    "rr": [1, 1],
    "bflyc": [1, 2, 1],
    "bflyp": [1, 2, 1],
    "ctree": [1, 1, 1],
    "ptree": [1, 1, 1],
    "condorc": [1, 1, 1, 1],
    "condorp": [1, 1, 1, 1],
    "ic": [1, 1, 1, 1],
    "ibfly": [1, 1, 1, 1],
    "box": [1, 1, 1, 1],
    "single": [1],
    "c": [1],
    "p": [1],
}


def apply_ratios(legs: list[dict], trade: TradeInput) -> list[dict]:
    """
    Apply volume ratios to a list of legs.

    If trade.ratios is set and matches the leg count, use custom ratios.
    Otherwise use the strategy's default ratios.
    Each ratio number multiplies the base volume (trade.volume).
    """
    # Determine which ratios to use
    if trade.ratios and len(trade.ratios) == len(legs):
        ratios = trade.ratios
    else:
        default = _DEFAULT_RATIOS.get(trade.strategy, [])
        if default and len(default) == len(legs):
            ratios = default
        else:
            return legs  # No applicable ratios, leave volumes as-is

    for i, leg in enumerate(legs):
        leg["volume"] = trade.volume * ratios[i]

    return legs


# =========================================================================
# Leg Builder Helper
# =========================================================================

def _build_leg(
    side: str,
    volume: int,
    trade: TradeInput,
    is_future: bool = False,
    strike: Optional[float] = None,
    option_type: Optional[str] = None,
    price: Optional[float] = None,
) -> dict:
    expiry_code = trade.contract_codes[0]
    type_code = trade.contract_codes[1] if len(trade.contract_codes) > 1 else expiry_code
    return {
        "side": side,
        "volume": volume,
        "market": "CME",
        "contract_type": get_contract_type(type_code, is_future),
        "expiry": get_expiry(expiry_code, is_future),
        "strike": round(strike, 4) if strike is not None else None,
        "option_type": option_type.upper() if option_type else None,
        "price": round(price, 4) if price is not None else None,
        "mo_card_code": get_card_mo_code(expiry_code, is_future),
        "package_premium": trade.premium,
        "suppress_premium": trade.suppress_premium,
    }


# =========================================================================
# Strategy Builders
# =========================================================================
# All builders use trade.volume as the base volume for every leg.
# apply_ratios() is called after to adjust volumes per the ratio.
# =========================================================================

def build_straddle(trade: TradeInput) -> list[dict]:
    k = trade.strikes[0]
    return [
        _build_leg("", trade.volume, trade, strike=k, option_type="P"),
        _build_leg("", trade.volume, trade, strike=k, option_type="C"),
    ]


def build_strangle(trade: TradeInput) -> list[dict]:
    lo = min(trade.strikes[0], trade.strikes[1])
    hi = max(trade.strikes[0], trade.strikes[1])
    return [
        _build_leg("", trade.volume, trade, strike=lo, option_type="P"),
        _build_leg("", trade.volume, trade, strike=hi, option_type="C"),
    ]


def build_call_spread(trade: TradeInput) -> list[dict]:
    strikes = sorted(trade.strikes[:2])
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    opt = trade.option_types[0] if trade.option_types else "C"
    return [
        _build_leg(buy, trade.volume, trade, strike=strikes[0], option_type=opt),
        _build_leg(sell, trade.volume, trade, strike=strikes[1], option_type=opt),
    ]


def build_put_spread(trade: TradeInput) -> list[dict]:
    strikes = sorted(trade.strikes[:2], reverse=True)
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    opt = trade.option_types[0] if trade.option_types else "P"
    return [
        _build_leg(buy, trade.volume, trade, strike=strikes[0], option_type=opt),
        _build_leg(sell, trade.volume, trade, strike=strikes[1], option_type=opt),
    ]


def build_risk_reversal(trade: TradeInput) -> list[dict]:
    lo = min(trade.strikes[0], trade.strikes[1])
    hi = max(trade.strikes[0], trade.strikes[1])
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=lo, option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=hi, option_type="C"),
    ]


def build_call_butterfly(trade: TradeInput) -> list[dict]:
    s = sorted(trade.strikes[:3])
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="C"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="C"),
        _build_leg(buy, trade.volume, trade, strike=s[2], option_type="C"),
    ]


def build_put_butterfly(trade: TradeInput) -> list[dict]:
    s = sorted(trade.strikes[:3])
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="P"),
        _build_leg(buy, trade.volume, trade, strike=s[2], option_type="P"),
    ]


def build_call_condor(trade: TradeInput) -> list[dict]:
    s = sorted(trade.strikes[:4])
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="C"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="C"),
        _build_leg(sell, trade.volume, trade, strike=s[2], option_type="C"),
        _build_leg(buy, trade.volume, trade, strike=s[3], option_type="C"),
    ]


def build_put_condor(trade: TradeInput) -> list[dict]:
    s = sorted(trade.strikes[:4], reverse=True)
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[2], option_type="P"),
        _build_leg(buy, trade.volume, trade, strike=s[3], option_type="P"),
    ]


def build_iron_condor(trade: TradeInput) -> list[dict]:
    s = sorted(trade.strikes[:4])
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[2], option_type="C"),
        _build_leg(buy, trade.volume, trade, strike=s[3], option_type="C"),
    ]


def build_iron_butterfly(trade: TradeInput) -> list[dict]:
    s = sorted(trade.strikes[:3])
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="C"),
        _build_leg(buy, trade.volume, trade, strike=s[2], option_type="C"),
    ]


def build_call_christmas_tree(trade: TradeInput) -> list[dict]:
    s = trade.strikes[:3]
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="C"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="C"),
        _build_leg(sell, trade.volume, trade, strike=s[2], option_type="C"),
    ]


def build_put_christmas_tree(trade: TradeInput) -> list[dict]:
    s = trade.strikes[:3]
    buy = trade.direction_side
    sell = "S" if buy == "B" else "B"
    return [
        _build_leg(buy, trade.volume, trade, strike=s[0], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[1], option_type="P"),
        _build_leg(sell, trade.volume, trade, strike=s[2], option_type="P"),
    ]


def build_box_spread(trade: TradeInput) -> list[dict]:
    lo = min(trade.strikes[0], trade.strikes[1])
    hi = max(trade.strikes[0], trade.strikes[1])
    return [
        _build_leg("B", trade.volume, trade, strike=lo, option_type="C"),
        _build_leg("S", trade.volume, trade, strike=hi, option_type="C"),
        _build_leg("S", trade.volume, trade, strike=lo, option_type="P"),
        _build_leg("B", trade.volume, trade, strike=hi, option_type="P"),
    ]


def build_single_option(trade: TradeInput) -> list[dict]:
    side = trade.direction_side
    opt = trade.option_types[0] if trade.option_types else "C"
    price = None if trade.suppress_premium else round(trade.premium, 4)
    return [
        _build_leg(side, trade.volume, trade, strike=trade.strikes[0],
                   option_type=opt, price=price),
    ]


def build_cvd_overlay(trade: TradeInput) -> list[dict]:
    fut_vol = round(trade.volume * trade.delta_percent / 100)
    px = trade.cvd_price

    if trade.strategy == "single":
        opt_type = trade.option_types[0].upper() if trade.option_types else ""
        if opt_type == "P":
            side_fut = trade.direction_side
        else:
            side_fut = "S" if trade.direction_side == "B" else "B"
    elif trade.is_put_centric:
        side_fut = trade.direction_side
    elif trade.is_call_centric:
        side_fut = "S" if trade.direction_side == "B" else "B"
    else:
        side_fut = "S" if trade.direction_side == "B" else "B"

    if trade.cvd_has_override:
        side_fut = "B" if trade.cvd_override_side == "+" else "S"
    if trade.delta_override == "B":
        side_fut = "B"
    if trade.delta_override == "S":
        side_fut = "S"

    return [
        _build_leg(side_fut, fut_vol, trade, is_future=True, price=px),
    ]


# =========================================================================
# Strategy Dispatcher
# =========================================================================

_STRATEGY_BUILDERS = {
    "straddle": build_straddle,
    "strangle": build_strangle,
    "cs": build_call_spread,
    "ps": build_put_spread,
    "rr": build_risk_reversal,
    "bflyc": build_call_butterfly,
    "bflyp": build_put_butterfly,
    "ctree": build_call_christmas_tree,
    "ptree": build_put_christmas_tree,
    "condorc": build_call_condor,
    "condorp": build_put_condor,
    "ic": build_iron_condor,
    "ibfly": build_iron_butterfly,
    "box": build_box_spread,
    "single": build_single_option,
    "c": build_single_option,
    "p": build_single_option,
}


def build_legs(trade: TradeInput) -> list[dict]:
    """
    Build all legs for a parsed TradeInput.

    1. Dispatch to strategy builder to get base legs
    2. Apply ratios (custom or default) to adjust volumes
    3. Stamp direction on straddle/strangle legs
    4. Append CVD overlay if present
    """
    builder = _STRATEGY_BUILDERS.get(trade.strategy)
    if builder is None:
        raise ValueError(f"Unrecognized strategy: '{trade.strategy}'")

    legs = builder(trade)

    # Stamp direction on straddle/strangle legs (both legs same side)
    if trade.strategy in ("straddle", "strangle"):
        for leg in legs:
            leg["side"] = trade.direction_side

    # Apply ratios to all option legs (not CVD overlay)
    legs = apply_ratios(legs, trade)

    # Append CVD overlay if present (after ratio application)
    if trade.is_cvd or trade.cvd_price != 0:
        legs.extend(build_cvd_overlay(trade))

    return legs