#!/usr/bin/env python3
"""Check current database state."""
import sys
sys.path.insert(0, '.')

from app.db import session_scope, create_database
from app.config import settings
from sqlalchemy import select, func
from app.models import (Match, Team, TeamRating, MatchPrediction,
                        PredictionSnapshot, DataSnapshot, TeamProfileMatchHistory)

create_database(settings.database_path)

with session_scope() as session:
    # Check matches
    matches = list(session.scalars(select(Match).order_by(Match.kickoff)))
    print(f'Total matches: {len(matches)}')
    final = [m for m in matches if m.status == 'final']
    scheduled = [m for m in matches if m.status == 'scheduled']
    print(f'Final: {len(final)}, Scheduled: {len(scheduled)}')

    # Check teams
    teams = list(session.scalars(select(Team)))
    print(f'Total teams: {len(teams)}')

    # Check ratings
    ratings_count = session.scalar(select(func.count(TeamRating.id)))
    print(f'Team ratings: {ratings_count}')

    # Check predictions
    pred_count = session.scalar(select(func.count(MatchPrediction.id)))
    print(f'Match predictions: {pred_count}')

    # Check snapshots
    snap_count = session.scalar(select(func.count(PredictionSnapshot.id)))
    print(f'Prediction snapshots: {snap_count}')

    # Check data snapshots
    data_snap_count = session.scalar(select(func.count(DataSnapshot.id)))
    print(f'Data snapshots: {data_snap_count}')

    # Check match history
    history_count = session.scalar(select(func.count(TeamProfileMatchHistory.id)))
    mock_count = session.scalar(
        select(func.count(TeamProfileMatchHistory.id))
        .where(TeamProfileMatchHistory.source == 'seed_mock_v1')
    )
    print(f'Match history: {history_count} (mock: {mock_count})')

    # Show final matches with scores
    print('\n--- Final Matches ---')
    for m in final[:10]:
        print(f'{m.home_team_id} {m.home_score} - {m.away_score} {m.away_team_id} (kickoff: {m.kickoff})')

    # Show upcoming matches
    print('\n--- Upcoming Matches ---')
    for m in scheduled[:10]:
        print(f'{m.home_team_id} vs {m.away_team_id} (kickoff: {m.kickoff})')

    # Check Elo ratings
    print('\n--- Top 10 Elo Ratings ---')
    ratings = list(session.scalars(
        select(TeamRating).order_by(TeamRating.elo.desc()).limit(10)
    ))
    for r in ratings:
        print(f'{r.team_id}: Elo={r.elo}, date={r.effective_date}')

    # Check prediction accuracy for final matches
    print('\n--- Prediction Accuracy ---')
    if final:
        for m in final[:5]:
            snap = session.scalar(
                select(PredictionSnapshot)
                .where(PredictionSnapshot.match_id == m.id)
                .order_by(PredictionSnapshot.created_at.desc())
                .limit(1)
            )
            if snap:
                actual = 'H' if m.home_score > m.away_score else 'D' if m.home_score == m.away_score else 'A'
                pred = 'H' if snap.home_win > max(snap.draw, snap.away_win) else 'D' if snap.draw > snap.away_win else 'A'
                hit = 'Y' if actual == pred else 'N'
                print(f'{m.home_team_id} {m.home_score}-{m.away_score} {m.away_team_id} | Pred: H={snap.home_win:.2f} D={snap.draw:.2f} A={snap.away_win:.2f} | {hit}')
