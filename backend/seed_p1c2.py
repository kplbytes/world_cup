# DEV/TEST ONLY: This script is for local simulation/verification and should not be run in production.
from datetime import datetime, timezone
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Match, MatchIntelligence, AutoAdjustment, ProviderQuotaState

engine = create_engine("sqlite:///data/world-cup.sqlite3")

with Session(engine) as session:
    # Get specific match
    match = session.query(Match).filter(Match.id == "2026-A-CZE-RSA-2026-06-18").first()
    if not match:
        print("No match found in DB!")
    else:
        # 1. Provider Quota States
        session.merge(ProviderQuotaState(
            provider="sporttery",
            reset_at=datetime.now(timezone.utc),
            daily_limit=5000,
            used_today=150
        ))
        session.merge(ProviderQuotaState(
            provider="api-football",
            reset_at=datetime.now(timezone.utc),
            daily_limit=100,
            used_today=100
        ))
        session.merge(ProviderQuotaState(
            provider="sportmonks",
            reset_at=datetime.now(timezone.utc),
            daily_limit=500,
            used_today=5
        ))

        # 2. Match Intelligence
        intel = MatchIntelligence(
            match_id=match.id,
            provider="sportmonks",
            source_url="http://mock",
            intelligence_type="injuries",
            affected_team_id=match.home_team_id,
            raw_payload={},
            normalized_payload={"players": ["Fake Player"]},
            source_confidence=0.8
        )
        session.add(intel)
        session.flush()

        # 3. Auto Adjustment
        adj = AutoAdjustment(
            match_id=match.id,
            source_intelligence_ids=[intel.id],
            affected_team_id=match.home_team_id,
            adjustment_type="roster_warning",
            attack_delta=0,
            defense_delta=0,
            confidence=-0.05,
            reason="测试伤停预警：主队存在伤停名单",
            model_version="test"
        )
        session.add(adj)

        session.commit()
        print("Dummy data seeded successfully for 2026-A-CZE-RSA-2026-06-18.")
