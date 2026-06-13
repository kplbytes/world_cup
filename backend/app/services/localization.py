from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team, TeamAlias


def localized_team_names(session: Session, teams: list[Team]) -> dict[str, str]:
    names = {team.id: team.short_name for team in teams}
    localized = {}
    for alias in session.scalars(
        select(TeamAlias)
        .where(TeamAlias.provider == "sporttery")
        .order_by(TeamAlias.id)
    ):
        localized.setdefault(alias.team_id, alias.alias)
    names.update(localized)
    return names
