"""Player importance lookup.

Production stub: returns None for all queries.
When a real player data source is integrated, replace this module's implementation.
"""
from pydantic import BaseModel


class PlayerMock(BaseModel):
    team_id: str
    player_name: str
    position: str
    importance_score: float
    is_key_player: bool
    source: str


def load_player_mocks() -> list[PlayerMock]:
    """No mock data in production. Returns empty list."""
    return []


def get_player_importance(player_name: str, team_id: str) -> PlayerMock | None:
    """No mock data in production. Always returns None."""
    return None
