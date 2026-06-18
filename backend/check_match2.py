import tempfile, os
from app.db import create_database
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import Match, PredictionSnapshot

tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, 'test.db')
engine = create_database(db_path)
session = Session(engine)

from app.services.recompute import recompute_all
recompute_all(session, iterations=100, seed=42)

from app.services.dashboard import build_dashboard
d = build_dashboard(session)
matches = d['groups'][0]['matches']
for i, m in enumerate(matches[:5]):
    print(f'[{i}] {m["id"]} kickoff={m["kickoff"]} status={m["status"]}')

match_id = matches[2]['id']
snaps = list(session.scalars(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id)))
print(f'match[2] snapshots: {len(snaps)}')
for s in snaps:
    print(f'  snapshotted_at={s.snapshotted_at} is_pre_match_locked={s.is_pre_match_locked}')

match_obj = session.get(Match, match_id)
print(f'match[2] kickoff={match_obj.kickoff}')

session.close()
os.unlink(db_path)
