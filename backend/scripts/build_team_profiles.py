from app.db import create_database, session_scope
from app.team_profiles.service import rebuild_team_profiles


if __name__ == "__main__":
    create_database()
    with session_scope() as session:
        print(rebuild_team_profiles(session, use_seed=True))
