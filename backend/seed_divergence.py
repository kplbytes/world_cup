from datetime import datetime, timezone
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import Match, MatchIntelligence, AutoAdjustment, PredictionSnapshot

engine = create_engine("sqlite:///data/world-cup.sqlite3")

with Session(engine) as session:
    match = session.query(Match).filter(Match.id == "2026-A-CZE-RSA-2026-06-18").first()
    if not match:
        print("No match found")
    else:
        # Mock MarketSnapshot
        from app.models import MarketSnapshot
        snap = session.query(MarketSnapshot).filter(MarketSnapshot.match_id == match.id).first()
        if not snap:
            snap = MarketSnapshot(
                match_id=match.id,
                provider="sporttery",
                home_probability=0.8,
                draw_probability=0.1,
                away_probability=0.1,
                raw_overround=1.05
            )
            session.add(snap)
        else:
            snap.home_probability = 0.8
            snap.draw_probability = 0.1
            snap.away_probability = 0.1
        session.flush()

        # Match Intelligence
        intel = MatchIntelligence(
            match_id=match.id,
            provider="sporttery",
            source_url="http://mock",
            intelligence_type="odds",
            affected_team_id=match.home_team_id,
            raw_payload={},
            normalized_payload={"home_win": 0.8, "draw": 0.1, "away_win": 0.1},
            source_confidence=0.9
        )
        session.add(intel)
        session.flush()

        # Auto Adjustment
        adj = AutoAdjustment(
            match_id=match.id,
            source_intelligence_ids=[intel.id],
            affected_team_id=match.home_team_id,
            adjustment_type="market_divergence",
            attack_delta=0,
            defense_delta=0,
            confidence=-0.1,
            reason="市场极端看好主队（隐含概率 80%），而模型预测仅为 51%，存在重大分歧",
            model_version="test"
        )
        session.add(adj)

        session.commit()
        print("Seeded market_divergence for CZE vs RSA")
