# =============================================================================
# Trade String Parser
# =============================================================================
# Parses SOFR options trade strings into structured TradeInput objects.
#
# SYNTAX OVERVIEW:
#   <contract_codes> <strategy> <strikes> [ratio] <price_format> [(notes)]
#
#   Price format determines direction:
#     price/qty  -> BUY  (debit)   e.g., 4/500
#     qty@price  -> SELL (credit)  e.g., 500@4
#
#   Ratios use X separator: 1X2, 1X3X2, 2X3X5, 1X2X2X1
#   Base volume from price format is multiplied by each ratio number.
#
#   VS keyword splits a trade into two segments, opposite directions (calendar)
#   WITH keyword splits a trade into two or more segments, SAME direction (stupid/strip)
#   [] wrapper groups multiple segments with a shared price/qty
#   Parenthetical notes at end are stripped and preserved in raw_input only.
#
#   STUPID / STRIP modifiers:
#     STUPID — 2-segment same-direction trade (use WITH to separate if needed)
#     STRIP  — N-segment same-direction trade (multi-contract same strike)
#     Both set is_stupid=True on each segment. For multi-contract same-strike
#     trades the contracts can be listed without WITH (e.g. SFRM7 SFRU7 SFRZ7).
#     For mixed-strategy trades use WITH to explicitly separate segments.
#
# REGULATORY NOTE: The parser must be deterministic. Same input = same output.
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import re
import copy


# =========================================================================
# Data Classes
# =========================================================================

@dataclass
class TradeInput:
    """
    Parsed representation of a single trade leg.
    """
    # --- Contract Identification ---
    contract_codes: list[str] = field(default_factory=list)
    strikes: list[float] = field(default_factory=list)
    option_types: list[str] = field(default_factory=list)

    # --- Strategy ---
    strategy: str = ""
    is_straddle: bool = False
    is_strangle: bool = False
    is_single_option: bool = False

    # --- Volume & Premium ---
    volume: int = 0
    premium: float = 0.0

    # --- CVD (Covered / Delta Hedge) ---
    is_cvd: bool = False
    cvd_price: float = 0.0
    cvd_has_override: bool = False
    cvd_override_side: str = ""

    # --- Direction ---
    direction_side: str = ""  # "B" or "S"

    # --- Delta ---
    delta_percent: float = 0.0
    delta_override: str = ""  # "B" or "S" if overridden

    # --- Centric Flags ---
    is_call_centric: bool = False
    is_put_centric: bool = False

    # --- Ratios ---
    # Flexible ratio list: [1,2] for a spread, [1,3,2] for a butterfly, etc.
    # Each number is a multiplier against the base volume.
    # Empty list means no custom ratio (use strategy defaults).
    ratios: list[int] = field(default_factory=list)

    # --- Misc ---
    leg_count: int = 0
    is_stupid: bool = False    # same direction on 2-segment trade
    is_strip: bool = False     # same direction on 3+ segment trade (display label only)
    suppress_premium: bool = False


class ParseError(Exception):
    """Raised when the trade string cannot be parsed."""
    pass


# =========================================================================
# Contract Code Helpers
# =========================================================================

_MONTH_CODES = set("FGHJKMNQUVXZ")
_PACK_HELPER_CODES = {"S0", "S2", "S3", "SR3"}


def is_pack_helper_code(code: str) -> bool:
    return code.upper().strip() in _PACK_HELPER_CODES


def is_contract_code(token: str) -> bool:
    u = token.upper().strip()
    if len(u) == 4:
        if u[0] in "0123" and u[1] in _MONTH_CODES and u[3].isdigit():
            return True
    if u[:3] in ("SR3", "SFR"):
        return True
    if u[:2] in ("S0", "S2", "S3"):
        return True
    return False


def add_pack_helper_if_short_dated(trade: TradeInput, code: str) -> None:
    u = code.upper().strip()
    if len(u) == 4 and u[0] in "023":
        pack_map = {"0": "S0", "2": "S2", "3": "S3"}
        helper = pack_map.get(u[0])
        if helper:
            trade.contract_codes.append(helper)


def leg_contains_code(trade: TradeInput, code: str) -> bool:
    target = code.upper()
    return any(c.upper() == target for c in trade.contract_codes)


# =========================================================================
# Ratio Parser
# =========================================================================

def parse_ratio_token(token: str) -> list[int]:
    """
    Parse a ratio token like '1X2', '1X3X2', '2X3X5X1'.
    Returns a list of integers. Returns empty list if not a valid ratio.
    """
    u = token.upper().strip()
    # Must contain at least one X and all parts must be digits
    if "X" not in u:
        return []
    parts = u.split("X")
    if len(parts) < 2:
        return []
    try:
        ratios = [int(p) for p in parts]
        if all(r > 0 for r in ratios):
            return ratios
    except ValueError:
        pass
    return []


def is_ratio_token(token: str) -> bool:
    """Check if a token is a valid ratio (NxN, NxNxN, etc.)."""
    return len(parse_ratio_token(token)) >= 2


# =========================================================================
# Strategy Token Handler
# =========================================================================

def set_strategy(trade: TradeInput, token: str, i: int, tokens: list[str]) -> int:
    """
    Attempt to interpret a token as a strategy keyword.
    Returns the number of additional tokens consumed.
    """
    u = token.upper().strip()

    # --- Two-word strategies (require peek-ahead) ---
    if u == "C" and i + 1 < len(tokens):
        peek = tokens[i + 1].upper().strip()
        if peek == "FLY":
            trade.strategy = "bflyc"; return 1
        elif peek == "CON":
            trade.strategy = "condorc"; return 1
        elif peek == "TREE":
            trade.strategy = "ctree"; return 1

    if u == "P" and i + 1 < len(tokens):
        peek = tokens[i + 1].upper().strip()
        if peek == "FLY":
            trade.strategy = "bflyp"; return 1
        elif peek == "CON":
            trade.strategy = "condorp"; return 1
        elif peek == "TREE":
            trade.strategy = "ptree"; return 1

    if u in ("IRON", "IRONCONDOR", "IRONCOND"):
        if i + 1 < len(tokens):
            peek = tokens[i + 1].upper().strip()
            if peek in ("CONDOR", "CON"):
                trade.strategy = "ic"; return 1
            elif peek == "FLY":
                trade.strategy = "ibfly"; return 1
            else:
                trade.strategy = "ic"
        else:
            trade.strategy = "ic"
        return 0

    # --- Single-word strategy tokens ---
    strategy_map = {
        "IC": "ic",
        "CS": "cs", "CALLSPREAD": "cs", "CALLSP": "cs", "CSPD": "cs",
        "PS": "ps", "PUTSPREAD": "ps", "PUTSP": "ps", "PSPD": "ps",
        "RR": "rr", "RISKREV": "rr", "RISKREVERSE": "rr", "RV": "rr",
        "CONDORC": "condorc", "CONC": "condorc", "CALLCONDOR": "condorc",
        "CONDORP": "condorp", "CONP": "condorp", "PUTCONDOR": "condorp",
        "BFLY": "bfly", "BUTTERFLY": "bfly", "FLY": "bfly",
        "BFLYC": "bflyc", "CALLBFLY": "bflyc", "CALLFLY": "bflyc", "BUTTERFLYC": "bflyc",
        "BFLYP": "bflyp", "PUTBFLY": "bflyp", "BUTTERFLYP": "bflyp",
        "TREE": "ctree", "CALLTREE": "ctree", "CTREE": "ctree", "TREEC": "ctree",
        "XMAS": "ctree", "CHRISTMAS": "ctree", "CALLXMAS": "ctree", "XMASC": "ctree",
        "PUTTREE": "ptree", "PTREE": "ptree", "TREEP": "ptree",
        "PUTXMAS": "ptree", "PUTCHRISTMAS": "ptree",
    }
    if u in strategy_map:
        trade.strategy = strategy_map[u]
        return 0

    # --- Stupid / Strip flag (same direction on all segments) ---
    if u == "STUPID":
        trade.is_stupid = True
        return 0
    if u == "STRIP":
        trade.is_stupid = True
        trade.is_strip = True
        return 0

    # --- Centric flags ---
    if u == "(CALLS)":
        trade.is_call_centric = True
        return 0
    if u == "(PUTS)":
        trade.is_put_centric = True
        return 0

    # --- CON / CONDOR with C/P qualifier ---
    if u in ("CON", "CONDOR") and i + 1 < len(tokens):
        peek = tokens[i + 1].upper().strip()
        if peek in ("C", "CALL"):
            trade.strategy = "condorc"; return 1
        elif peek in ("P", "PUT"):
            trade.strategy = "condorp"; return 1

    # --- Ratio tokens (flexible: NxN, NxNxN, NxNxNxN, etc.) ---
    ratios = parse_ratio_token(u)
    if ratios:
        trade.ratios = ratios
        return 0

    # --- CVD token ---
    if u == "CVD":
        trade.is_cvd = True
        if i + 1 < len(tokens):
            cvd_tok = tokens[i + 1].strip()
            cvd_num = cvd_tok
            cvd_override = ""
            paren_match = re.match(r"^(.+?)\(([+-])\)$", cvd_tok)
            if paren_match:
                cvd_num = paren_match.group(1)
                cvd_override = paren_match.group(2)
            try:
                trade.cvd_price = float(cvd_num)
                if cvd_override in ("+", "-"):
                    trade.cvd_has_override = True
                    trade.cvd_override_side = cvd_override
                return 1
            except ValueError:
                raise ParseError(f"CVD: expected a valid price after 'CVD', got '{cvd_tok}'.")
        else:
            raise ParseError("CVD token at end of input with no price.")

    # --- Delta token ---
    if u == "D":
        if i + 1 < len(tokens):
            try:
                trade.delta_percent = float(tokens[i + 1])
                consumed = 1
                if i + 2 < len(tokens):
                    ot = tokens[i + 2].strip()
                    if ot == "(+)":
                        trade.delta_override = "B"; consumed = 2
                    elif ot == "(-)":
                        trade.delta_override = "S"; consumed = 2
                return consumed
            except ValueError:
                raise ParseError(f"D token: expected a number, got '{tokens[i + 1]}'.")
        else:
            raise ParseError("D token at end of input with no value.")

    return 0


# =========================================================================
# Single Leg Parser
# =========================================================================

def parse_single_leg(tokens: list[str]) -> TradeInput:
    """
    Parse a list of tokens into a single TradeInput leg.
    """
    trade = TradeInput()
    has_call = False
    has_put = False
    i = 0

    while i < len(tokens):
        token = tokens[i].strip()
        if not token:
            i += 1
            continue
        u = token.upper()

        # --- CVD token ---
        if u == "CVD":
            consumed = set_strategy(trade, token, i, tokens)
            i += 1 + consumed
            continue

        # --- Delta token ---
        if u == "D":
            if i + 1 < len(tokens):
                d_tok = tokens[i + 1].strip()
                try:
                    trade.delta_percent = float(d_tok)
                    i += 2
                    if i < len(tokens):
                        ot = tokens[i].strip()
                        if ot == "(+)":
                            trade.delta_override = "B"; i += 1
                        elif ot == "(-)":
                            trade.delta_override = "S"; i += 1
                    continue
                except ValueError:
                    pass
            i += 1
            continue

        # --- Contract code ---
        if is_contract_code(u):
            trade.contract_codes.append(token)
            add_pack_helper_if_short_dated(trade, token)
            i += 1
            continue

        # --- Strike (decimal number) ---
        try:
            val = float(token)
            if "." in token:
                prev = tokens[i - 1].upper().strip() if i > 0 else ""
                if prev not in ("CVD", "D"):
                    trade.strikes.append(val)
                    i += 1
                    continue
        except ValueError:
            pass

        # --- Straddle (^) ---
        if u == "^":
            trade.is_straddle = True
            trade.strategy = "straddle"
            trade.option_types = ["P", "C"]
            i += 1
            continue

        # --- Strangle (^^) ---
        if u == "^^":
            trade.is_strangle = True
            trade.strategy = "strangle"
            trade.option_types = ["P", "C"]
            i += 1
            continue

        # --- Option type flags ---
        if u in ("C", "CALL"):
            has_call = True
        if u in ("P", "PUT"):
            has_put = True

        # --- Strategy tokens (including ratios) ---
        consumed = set_strategy(trade, token, i, tokens)
        i += 1 + consumed

    # -----------------------------------------------------------------
    # Post-parse: resolve strategy from option type flags
    # -----------------------------------------------------------------
    multi_leg_strategies = {
        "bfly", "ctree", "ptree", "condor", "condorc", "condorp",
        "ibfly", "cs", "ps",
    }
    if trade.strategy in multi_leg_strategies:
        if has_put and not has_call:
            if trade.strategy in ("bfly", "bflyc"):
                trade.strategy = "bflyp"
            if trade.strategy == "ctree":
                trade.strategy = "ptree"
            if trade.strategy in ("condor", "condorc"):
                trade.strategy = "condorp"
            if not trade.option_types:
                trade.option_types.append("P")
        else:
            if trade.strategy in ("bfly", "bflyp"):
                trade.strategy = "bflyc"
            if trade.strategy == "ptree":
                trade.strategy = "ctree"
            if trade.strategy in ("condor", "condorp"):
                trade.strategy = "condorc"
            if not trade.option_types:
                trade.option_types.append("C")

    # Ratio spreads without explicit strategy
    if not trade.strategy and trade.ratios and len(trade.ratios) == 2:
        if has_call and not has_put:
            trade.strategy = "cs"
            trade.option_types = ["C"]
            trade.is_call_centric = True
        elif has_put and not has_call:
            trade.strategy = "ps"
            trade.option_types = ["P"]
            trade.is_put_centric = True

    # cs/ps have unambiguous option types — force them regardless of has_call/has_put.
    # The multi-leg resolution block above only appends "C"/"P" if option_types is empty,
    # which can mis-fire for PS (sets "C") when the P token is part of the keyword.
    if trade.strategy == "cs":
        trade.option_types = ["C"]
    elif trade.strategy == "ps":
        trade.option_types = ["P"]

    # Set centric flags based on strategy
    call_centric = {"bflyc", "ctree", "condorc", "ibfly", "cs"}
    put_centric = {"bflyp", "ptree", "condorp", "ps"}
    if trade.strategy in call_centric:
        trade.is_call_centric = True
    if trade.strategy in put_centric:
        trade.is_put_centric = True

    # Fallback: single option
    if not trade.strategy:
        if has_call and not has_put:
            trade.strategy = "single"
            trade.option_types = ["C"]
        elif has_put and not has_call:
            trade.strategy = "single"
            trade.option_types = ["P"]
        else:
            raise ParseError("Could not determine strategy. No C/P or strategy token found.")

    # Validate contract codes
    if not trade.contract_codes:
        raise ParseError("No contract code found in the trade string.")

    # Validate strikes
    if not trade.strikes:
        raise ParseError("No strikes found in the trade string.")

    return trade


# =========================================================================
# Strike Validation
# =========================================================================

def validate_strikes(trade: TradeInput) -> bool:
    expected = {
        "cs": (1, 2), "ps": (1, 2), "strangle": (1, 2), "rr": (1, 2),
        "bflyc": (3, 3), "bflyp": (3, 3), "ctree": (3, 3), "ptree": (3, 3),
        "condorc": (4, 4), "condorp": (4, 4), "ic": (4, 4),
        "straddle": (1, None), "single": (1, None), "c": (1, None), "p": (1, None),
    }
    if trade.strategy in expected:
        lo, hi = expected[trade.strategy]
        count = len(trade.strikes)
        if hi is None:
            if count < lo:
                raise ParseError(f"Strategy '{trade.strategy}' requires at least {lo} strike(s), found {count}.")
        else:
            if not (lo <= count <= hi):
                raise ParseError(f"Strategy '{trade.strategy}' requires {lo}-{hi} strike(s), found {count}.")
    return True


# =========================================================================
# Parenthetical Stripper
# =========================================================================

def strip_trailing_parenthetical(input_line: str) -> str:
    """
    Remove a trailing parenthetical note from the trade string.
    These are human reminders like (96.50) or (2 legs) that should
    not be parsed. Only strips if it's at the end after the price format.

    Examples:
      'sfrh7 96.25 96.50 c 1x2 4/500 (96.50)' -> 'sfrh7 96.25 96.50 c 1x2 4/500'
      'sfrh7 96.25 ^ 3/100 (2x call)' -> 'sfrh7 96.25 ^ 3/100'
    """
    # Match a parenthetical at the end, after whitespace.
    # Exclude functional operator tokens (+) and (-) which are used as
    # direction overrides on CVD prices and D delta tokens.
    match = re.search(r'\s+\((?![+-]\))[^)]*\)\s*$', input_line)
    if match:
        return input_line[:match.start()].strip()
    return input_line


# =========================================================================
# Bracket Wrapper Parser
# =========================================================================

def parse_bracket_wrapper(input_line: str) -> list[TradeInput]:
    close_pos = input_line.find("]")
    if close_pos == -1:
        raise ParseError("[] syntax error: missing ']'.")
    inner = input_line[1:close_pos].strip()
    trailer = input_line[close_pos + 1:].strip()
    if not inner:
        raise ParseError("[] syntax error: empty brackets.")
    if not trailer:
        raise ParseError("[] syntax error: no price/qty after ']'.")

    if "@" in trailer:
        pkg_side = "S"
        parts = trailer.split("@")
        pkg_vol = int(float(parts[0].strip()))
        pkg_prem = float(parts[1].strip())
    elif "/" in trailer:
        pkg_side = "B"
        parts = trailer.split("/")
        pkg_prem = float(parts[0].strip())
        pkg_vol = int(float(parts[1].strip()))
    else:
        raise ParseError(f"[] syntax error: cannot parse trailer '{trailer}'.")

    if pkg_vol == 0:
        raise ParseError("[] syntax error: volume = 0.")

    result = []
    segments = inner.split(",")
    for seg in segments:
        seg = seg.strip()
        if seg:
            # Normalise whitespace within the segment the same way parse_trade_input does,
            # then call parse_single_leg directly — segments have no price/qty token.
            seg = re.sub(r'\s+', ' ', seg).strip()
            seg_tokens = seg.split(' ')
            t = parse_single_leg(seg_tokens)
            validate_strikes(t)
            t.direction_side = pkg_side
            t.suppress_premium = True
            t.volume = pkg_vol
            t.premium = round(pkg_prem * 0.01, 4)
            result.append(t)
    return result


# =========================================================================
# Main Parser Entry Point
# =========================================================================

def parse_trade_input(input_line: str) -> list[TradeInput]:
    """
    Parse a trade string into a list of TradeInput objects.
    """
    input_line = input_line.strip()
    if not input_line:
        raise ParseError("Empty trade string.")

    # --- Bracket wrapper ---
    if input_line.startswith("["):
        return parse_bracket_wrapper(input_line)

    # --- Strip trailing parenthetical notes ---
    input_line = strip_trailing_parenthetical(input_line)

    # --- Normalize whitespace around @ and / ---
    input_line = re.sub(r"\s*@\s*", "@", input_line)
    input_line = re.sub(r"\s*/\s*", "/", input_line)
    input_line = re.sub(r"\s+", " ", input_line).strip()

    tokens = input_line.split(" ")

    # --- First pass: extract volume, premium, side.
    # Collect remaining tokens into `parts`, preserving VS / WITH as separators.
    parsed_side = ""
    parsed_volume = 0
    parsed_premium = 0.0
    parts = []

    for idx, raw in enumerate(tokens):
        raw = raw.strip()
        if not raw:
            continue

        # Keep VS and WITH as literal separator tokens in parts
        if raw.upper() in ("VS", "WITH"):
            parts.append(raw)
            continue

        # Price/qty format: price/qty -> BUY
        if "/" in raw and "@" not in raw:
            slash_parts = raw.split("/")
            if len(slash_parts) == 2:
                try:
                    parsed_premium = round(float(slash_parts[0]) * 0.01, 4)
                    parsed_volume = int(float(slash_parts[1]))
                    parsed_side = "B"
                    continue
                except ValueError:
                    pass

        # Qty@price format: qty@price -> SELL
        if "@" in raw:
            at_parts = raw.split("@")
            if len(at_parts) == 2:
                try:
                    parsed_volume = int(float(at_parts[0]))
                    parsed_premium = round(float(at_parts[1]) * 0.01, 4)
                    parsed_side = "S"
                    continue
                except ValueError:
                    pass

        parts.append(raw)

    if parsed_volume == 0:
        raise ParseError(
            "No volume found in trade string. "
            "Use price/qty format (e.g., 4/500) or qty@price format (e.g., 500@4)."
        )

    if parsed_side not in ("B", "S"):
        raise ParseError(
            "Could not determine buy/sell direction. "
            "Use price/qty for a debit (buy) or qty@price for a credit (sell)."
        )

    parts = [p for p in parts if p.strip()]

    # Detect separator keywords in parts
    separator_indices = [i for i, p in enumerate(parts) if p.upper() in ("VS", "WITH")]
    has_separator = len(separator_indices) > 0
    # Determine separator type: any WITH → same direction; pure VS → flip direction
    separator_types = {parts[i].upper() for i in separator_indices}
    with_mode = "WITH" in separator_types
    vs_mode = "VS" in separator_types and not with_mode  # VS only if no WITH present

    # =====================================================================
    # NO SEPARATOR — single segment or multi-contract implicit split
    # =====================================================================
    if not has_separator:
        leg1 = parse_single_leg(parts)
        leg1.volume = parsed_volume
        leg1.premium = parsed_premium
        leg1.direction_side = parsed_side

        # Collect real (non-helper) contract codes
        real_codes = [c for c in leg1.contract_codes if not is_pack_helper_code(c)]

        # --- Multi-contract handling ---
        if len(real_codes) > 1:
            if leg1.is_stupid:
                # STUPID/STRIP with N contracts: each contract gets its own segment,
                # all same direction.
                result = []
                for code in real_codes:
                    dup = copy.deepcopy(leg1)
                    dup.contract_codes = [code]
                    add_pack_helper_if_short_dated(dup, code)
                    dup.direction_side = leg1.direction_side
                    dup.suppress_premium = True
                    result.append(dup)
                return result

            if leg1.strategy in ("cs", "ps") and len(leg1.strikes) == 1:
                # Calendar spread: two contracts, same strike, opposite directions
                result = []
                for idx, code in enumerate(real_codes):
                    cal = copy.deepcopy(leg1)
                    cal.strategy = "single"
                    cal.contract_codes = [code]
                    add_pack_helper_if_short_dated(cal, code)
                    if leg1.strategy == "ps":
                        cal.option_types = ["P"]
                        cal.is_put_centric = True
                        cal.is_call_centric = False
                    else:
                        cal.option_types = ["C"]
                        cal.is_call_centric = True
                        cal.is_put_centric = False
                    if idx == 0:
                        cal.direction_side = parsed_side
                    else:
                        cal.direction_side = "S" if parsed_side == "B" else "B"
                    result.append(cal)
                return result

        # Stupid with multiple strikes on a single-option strategy:
        # e.g. SFRU6 96.00 96.25 C stupid 4/500 → buy 96.00 C AND buy 96.25 C.
        if leg1.is_stupid and leg1.strategy == "single" and len(leg1.strikes) > 1:
            result = []
            for strike in leg1.strikes:
                dup = copy.deepcopy(leg1)
                dup.strikes = [strike]
                dup.direction_side = parsed_side
                dup.suppress_premium = True
                result.append(dup)
            return result

        validate_strikes(leg1)
        return [leg1]

    # =====================================================================
    # WITH / VS — explicit segment separator(s)
    # Splits parts into N segments at each VS or WITH boundary.
    # WITH: all segments same direction (stupid/strip mode)
    # VS:   first segment takes parsed_side, all others flip (calendar mode)
    # Mixed VS+WITH is not supported; WITH takes precedence if present.
    # =====================================================================
    if separator_indices[0] == 0:
        raise ParseError("WITH/VS found at start of input with no left segment.")
    if separator_indices[-1] == len(parts) - 1:
        raise ParseError("WITH/VS found at end of input with no right segment.")

    # Split parts into segment token lists at each separator
    segment_token_lists = []
    current = []
    for p in parts:
        if p.upper() in ("VS", "WITH"):
            if current:
                segment_token_lists.append(current)
            current = []
        else:
            current.append(p)
    if current:
        segment_token_lists.append(current)

    if len(segment_token_lists) < 2:
        raise ParseError("WITH/VS found but could not split into segments.")

    # Parse each segment independently
    result = []
    for seg_idx, seg_tokens in enumerate(segment_token_lists):
        seg = parse_single_leg(seg_tokens)
        seg.volume = parsed_volume
        seg.premium = parsed_premium
        seg.suppress_premium = True

        # Direction: WITH → all same; VS → flip all after first;
        # stupid flag on any segment also forces same direction for VS
        any_stupid = any(s.is_stupid for s in result) or seg.is_stupid
        if with_mode or any_stupid:
            seg.direction_side = parsed_side
        elif vs_mode and seg_idx > 0:
            seg.direction_side = "S" if parsed_side == "B" else "B"
        else:
            seg.direction_side = parsed_side

        # Within an explicit WITH/VS segment, cs/ps must have exactly 2 strikes.
        # The (1,2) allowance in validate_strikes exists only for the implicit
        # calendar spread path (multi-contract, no separator). Here each segment
        # is already fully specified, so 1 strike on a spread is an error.
        real_codes = [c for c in seg.contract_codes if not is_pack_helper_code(c)]
        if seg.strategy in ("cs", "ps") and len(seg.strikes) == 1 and len(real_codes) == 1:
            raise ParseError(
                f"Segment {seg_idx + 1}: strategy '{seg.strategy}' requires 2 strikes "
                f"when used in a WITH/VS trade, found 1."
            )

        validate_strikes(seg)
        result.append(seg)

    return result