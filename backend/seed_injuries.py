from app.models import Match, MatchIntelligence
from app.db import session_scope

with session_scope() as session:
    match = session.query(Match).filter(Match.id == "2026-A-CZE-RSA-2026-06-18").first()
    if match:
        intel = MatchIntelligence(
            match_id=match.id,
            provider="sportmonks",
            source_url="",
            intelligence_type="injuries",
            affected_team_id=match.home_team_id,
            raw_payload={},
            normalized_payload={
                "affected_team_id": "CZE",
                "player_name": "Star Defender",
                "reason": "Hamstring Injury"
            },
            source_confidence=0.9
        )
        session.add(intel)
        session.commit()
        print(f"Injected injury for CZE in match {match.id}")
