#!/usr/bin/env python3
"""Update FIFA rankings with latest 2026-06 data from official FIFA real-time ranking."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import session_scope
from app.models import Team, TeamRating
from sqlalchemy import select

# Latest FIFA rankings (2026-06-18 real-time update after WC group stage round 1)
# Source: FIFA official real-time ranking + The Athletic's 48-team list
FIFA_RANKINGS_2026 = {
    "ARG": 1, "FRA": 2, "ESP": 3, "ENG": 4, "BRA": 5,
    "MAR": 6, "POR": 7, "NED": 8, "GER": 9, "BEL": 10,
    "COL": 11, "MEX": 13, "CRO": 14, "SEN": 15, "USA": 16,
    "JPN": 17, "URU": 18, "SUI": 19, "IRN": 20, "KOR": 22,
    "TUR": 23, "ECU": 24, "AUT": 25, "AUS": 27, "ALG": 28,
    "EGY": 29, "CAN": 30, "NOR": 31, "CIV": 33, "PAN": 34,
    "TUN": 45, "CZE": 43, "PAR": 40, "SCO": 41, "KSA": 60,
    "QAT": 56, "IRQ": 57, "UZB": 50, "COD": 46, "RSA": 61,
    "GHA": 73, "JOR": 63, "CPV": 67, "BIH": 64, "NZL": 85,
    "HAI": 83, "CUW": 82, "SWE": 38,
}

FIFA_POINTS_2026 = {
    "ARG": 1865.0, "FRA": 1852.0, "ESP": 1837.0, "ENG": 1817.0,
    "BRA": 1772.0, "MAR": 1760.0, "POR": 1752.0, "NED": 1740.0,
    "GER": 1692.0, "BEL": 1768.0, "COL": 1739.0, "MEX": 1636.0,
    "CRO": 1700.0, "SEN": 1621.0, "USA": 1632.0, "JPN": 1640.0,
    "URU": 1701.0, "SUI": 1641.0, "IRN": 1623.0, "KOR": 1573.0,
    "TUR": 1539.0, "ECU": 1535.0, "AUT": 1580.0, "AUS": 1544.0,
    "ALG": 1486.0, "EGY": 1516.0, "CAN": 1502.0, "NOR": 1528.0,
    "CIV": 1509.0, "PAN": 1503.0, "TUN": 1505.0, "CZE": 1473.0,
    "PAR": 1424.0, "SCO": 1462.0, "KSA": 1433.0, "QAT": 1482.0,
    "IRQ": 1436.0, "UZB": 1413.0, "COD": 1417.0, "RSA": 1415.0,
    "GHA": 1360.0, "JOR": 1378.0, "CPV": 1379.0, "BIH": 1332.0,
    "NZL": 1247.0, "HAI": 1279.0, "CUW": 1261.0, "SWE": 1472.0,
}


def main():
    with session_scope() as session:
        teams = list(session.scalars(select(Team)))
        updated = 0
        not_found = []

        for team in teams:
            tid = team.id
            rank = FIFA_RANKINGS_2026.get(tid)
            points = FIFA_POINTS_2026.get(tid)

            if rank is None:
                not_found.append(tid)
                continue

            rating = session.scalar(
                select(TeamRating)
                .where(TeamRating.team_id == tid)
                .order_by(TeamRating.effective_date.desc())
                .limit(1)
            )
            if rating is None:
                continue

            old_rank = rating.fifa_rank
            rating.fifa_rank = rank
            rating.fifa_points = points
            updated += 1
            if old_rank != rank:
                print(f"  {tid}: rank {old_rank} -> {rank}, points={points}")

        print(f"\nUpdated {updated} teams")
        if not_found:
            print(f"Missing FIFA rank for: {not_found}")


if __name__ == "__main__":
    main()
