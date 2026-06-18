from datetime import datetime, timezone, timedelta
from app.db import create_database, session_scope
from app.models import Match, TeamProfile
from app.team_profiles.service import get_team_profile
from sqlalchemy import select

create_database()
now = datetime.now(timezone.utc)
cutoff = now + timedelta(hours=24)

with session_scope() as session:
    matches = list(session.scalars(
        select(Match)
        .where(Match.status != "final")
        .where(Match.kickoff >= now)
        .where(Match.kickoff <= cutoff)
        .order_by(Match.kickoff)
    ))
    
    print(f"Future 24h matches: {len(matches)}")
    print()
    
    seen_teams = set()
    for m in matches:
        for tid in [m.home_team_id, m.away_team_id]:
            if tid and tid not in seen_teams:
                seen_teams.add(tid)
                profile = get_team_profile(session, tid, allow_mock=False)
                if profile:
                    print(f"{profile.team_code:20s} | {tid:8s} | {profile.profile_version:25s} | src={profile.sample_count:3d} | recent={profile.recent_match_count:3d} | {profile.data_quality:12s} | conf={profile.confidence:.3f}")
                else:
                    print(f"{'???':20s} | {tid:8s} | {'NO REAL PROFILE':25s} |")
