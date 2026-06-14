from typing import Protocol, Any
from sqlalchemy.orm import Session


class IntelligenceProvider(Protocol):
    @property
    def name(self) -> str: ...

    def fetch_intelligence(
        self, session: Session, match_id: str, kickoff: Any, home_team_id: str, away_team_id: str
    ) -> list[int]:
        """Fetch intelligence for a match and save it.

        Must respect quota_guard and cache.
        Returns a list of inserted MatchIntelligence IDs.
        """
        ...
