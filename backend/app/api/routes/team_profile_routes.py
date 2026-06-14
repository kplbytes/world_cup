from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.db import session_scope
from app.models import Team, TeamProfilePrediction
from app.team_profiles.evaluation import evaluate_profile_model
from app.team_profiles.service import explain_team_profile, get_team_profile, profile_payload, rebuild_team_profiles


router = APIRouter()


@router.get("/team-profiles")
def team_profiles():
    with session_scope() as session:
        rows = []
        for team in session.scalars(select(Team).order_by(Team.group_code, Team.code)):
            profile = get_team_profile(session, team.id)
            rows.append({"team": {"id": team.id, "code": team.code, "name": team.short_name}, "profile": profile_payload(profile), "summary": explain_team_profile(profile) if profile else "暂无画像"})
        return {"profiles": rows}


@router.get("/team-profiles/evaluation")
def profile_evaluation():
    with session_scope() as session:
        return evaluate_profile_model(session)


@router.get("/team-profiles/{team_id}")
def team_profile(team_id: str, as_of: datetime | None = None):
    with session_scope() as session:
        profile = get_team_profile(session, team_id, as_of)
        if profile is None:
            raise HTTPException(status_code=404, detail="team profile not found")
        return {"profile": profile_payload(profile), "summary": explain_team_profile(profile)}


@router.post("/team-profiles/rebuild")
def rebuild_profiles(use_seed: bool = True):
    with session_scope() as session:
        return rebuild_team_profiles(session, datetime.now(timezone.utc), use_seed=use_seed)


@router.get("/team-profile-predictions/{match_id}")
def profile_prediction(match_id: str):
    with session_scope() as session:
        row = session.scalar(select(TeamProfilePrediction).where(TeamProfilePrediction.match_id == match_id).order_by(TeamProfilePrediction.created_at.desc()).limit(1))
        if row is None:
            return {"match_id": match_id, "prediction": None}
        return {"match_id": match_id, "prediction": {column.name: getattr(row, column.name) for column in TeamProfilePrediction.__table__.columns}}
