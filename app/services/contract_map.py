# =============================================================================
# Contract Map
# =============================================================================
# Maps short-dated SOFR contract codes to their pack series (S0, S2, S3)
# and pack offsets.
#
# Port of ContractMap_v4.bas from the VBA tool. The VBA version used a
# fixed array of 12 entries; this version uses a dictionary for O(1) lookup.
#
# NOTE: This table is updated periodically as new contract codes roll on.
# When CME lists new short-dated SOFR options series, add entries here.
# =============================================================================

from dataclasses import dataclass


@dataclass(frozen=True)
class PackMapping:
    """A single contract-to-pack mapping entry."""
    code: str      # Contract code (e.g., "0QZ5")
    pack: str      # Pack series (e.g., "S0")
    offset: int    # Pack offset (e.g., 1)


# -------------------------------------------------------------------------
# Pack Mapping Table
# -------------------------------------------------------------------------
# Each entry maps a short-dated contract code to its pack series and offset.
# The offset indicates how many years forward the underlying quarterly
# future settles relative to the option expiry.
#
# REGULATORY NOTE: Incorrect pack mappings will cause trades to be reported
# with the wrong contract type. Verify against CME's contract specifications
# when updating this table.
# -------------------------------------------------------------------------
_PACK_TABLE: list[PackMapping] = [
    PackMapping("0QZ5", "S0", 1),
    PackMapping("0QF6", "S0", 1),
    PackMapping("0QG6", "S0", 1),
    PackMapping("0QH6", "S0", 1),
    PackMapping("2QM6", "S2", 2),
    PackMapping("2QN6", "S2", 2),
    PackMapping("2QQ6", "S2", 2),
    PackMapping("2QU6", "S2", 2),
    PackMapping("3QU6", "S3", 3),
    PackMapping("3QV6", "S3", 3),
    PackMapping("3QX6", "S3", 3),
    PackMapping("3QZ6", "S3", 3),
]

# Build lookup dictionaries for O(1) access
_CODE_TO_PACK: dict[str, PackMapping] = {
    entry.code.upper(): entry for entry in _PACK_TABLE
}


def is_short_dated_contract(code: str) -> bool:
    """Check if a contract code is in the short-dated pack mapping table."""
    return code.upper() in _CODE_TO_PACK


def pack_code_from_short_dated(code: str) -> str:
    """
    Get the pack series code (S0, S2, S3) for a short-dated contract.
    Returns empty string if the code is not in the table.
    """
    entry = _CODE_TO_PACK.get(code.upper())
    return entry.pack if entry else ""


def pack_offset_from_short_dated(code: str) -> int:
    """
    Get the pack offset for a short-dated contract.
    Returns 0 if the code is not in the table.
    """
    entry = _CODE_TO_PACK.get(code.upper())
    return entry.offset if entry else 0
