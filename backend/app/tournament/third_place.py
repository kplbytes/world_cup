from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path


logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
THIRD_PLACE_TABLE_PATH = ROOT / "data" / "seed" / "world-cup-2026-third-place-combinations.json"

# Table column order on the published combination table:
# 1A, 1B, 1D, 1E, 1G, 1I, 1K, 1L
SLOT_TO_MATCH_NUMBER = {
    "A1": 79,
    "B1": 85,
    "D1": 81,
    "E1": 74,
    "G1": 82,
    "I1": 77,
    "K1": 87,
    "L1": 80,
}

MATCH_TO_SLOT = {match_number: slot for slot, match_number in SLOT_TO_MATCH_NUMBER.items()}


@lru_cache(maxsize=1)
def load_third_place_combination_table() -> dict[str, dict[str, str]]:
    payload = json.loads(THIRD_PLACE_TABLE_PATH.read_text(encoding="utf-8"))
    return payload["combinations"]


def official_match_group_allocation(qualified_groups: list[str]) -> dict[int, str] | None:
    """Return official match_number -> third-place group allocation for 8 groups.

    Returns None if the group set is incomplete or missing from the published table.
    """
    normalized = sorted({group.strip().upper() for group in qualified_groups if group})
    if len(normalized) != 8:
        return None

    combo_key = "".join(normalized)
    assignments = load_third_place_combination_table().get(combo_key)
    if assignments is None:
        logger.warning("third-place combination %s not found in published allocation table", combo_key)
        return None

    return {
        SLOT_TO_MATCH_NUMBER[slot]: source[1:]
        for slot, source in assignments.items()
        if slot in SLOT_TO_MATCH_NUMBER and source.startswith("3") and len(source) == 2
    }
