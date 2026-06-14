"""Mock Intelligence Provider - ONLY for testing.

This provider is disabled in production. It will raise RuntimeError
if accidentally instantiated outside of test environments.
"""
import os
from typing import Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.intelligence.providers.base import IntelligenceProvider
from app.intelligence.cache import save_intelligence


class MockProvider(IntelligenceProvider):
    """Mock provider for testing only. Raises error in production."""

    def __init__(self):
        if os.environ.get("APP_MODE", "local") != "test":
            raise RuntimeError(
                "MockProvider must NOT be used in production. "
                "Set APP_MODE=test to enable it for testing."
            )

    @property
    def name(self) -> str:
        return "mock"

    def fetch_intelligence(
        self, session: Session, match_id: str, kickoff: Any, home_team_id: str, away_team_id: str
    ) -> list[int]:
        now = datetime.now(timezone.utc)

        row1 = save_intelligence(
            session=session,
            match_id=match_id,
            provider=self.name,
            source_url="http://mock.local/injuries",
            intelligence_type="injury",
            raw_payload={"status": "injured", "player": "Fake Star"},
            normalized_payload={"impact": "high"},
            source_confidence=0.8,
            affected_team_id=home_team_id,
            affected_player_name="Fake Star"
        )

        return [row1.id]
