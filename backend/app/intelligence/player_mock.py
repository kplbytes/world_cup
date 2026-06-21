"""Player importance lookup.

Loads curated player importance data from data/seed/player_importance_curated.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel


class PlayerMock(BaseModel):
    team_id: str
    player_name: str
    position: str
    importance_score: float
    is_key_player: bool
    source: str


_DATA_PATH = Path(__file__).resolve().parents[3] / "data" / "seed" / "player_importance_curated.json"

_cache: list[PlayerMock] | None = None


def load_player_importance() -> list[PlayerMock]:
    """Load curated player importance data from JSON file (cached after first call)."""
    global _cache
    if _cache is not None:
        return _cache
    with open(_DATA_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    _cache = [PlayerMock(**item) for item in raw]
    return _cache


def get_player_importance(player_name: str, team_id: str) -> PlayerMock | None:
    """Look up a player by name and team_id."""
    for player in load_player_importance():
        if player.player_name == player_name and player.team_id == team_id:
            return player
    return None
