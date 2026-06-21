#!/usr/bin/env python3
"""Populate FIFA rankings into TeamRating table from external CSV data."""

import csv
import json
from datetime import datetime, timezone
from hashlib import sha256
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.db import session_scope
from app.models import DataSnapshot, Team, TeamRating
from sqlalchemy import select

FIFA_CSV_PROVIDER = "fifa_ranking_csv"
FIFA_OFFICIAL_PROVIDER = "fifa_official_ranking"
FIFA_OFFICIAL_URL = "https://api.fifa.com/api/v3/rankings?gender=1&count=300"


def parse_official_fifa_rankings(payload: dict) -> dict:
    rankings = {}
    dates = []
    for row in payload.get("Results", []):
        code = str(row.get("IdCountry") or "").strip()
        rank = row.get("Rank")
        points = row.get("DecimalTotalPoints", row.get("TotalPoints"))
        pub_date = row.get("PubDate")
        if not code or rank is None or points is None:
            continue
        rankings[code] = {"rank": int(rank), "points": float(points)}
        if pub_date:
            dates.append(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).date().isoformat())
    if not dates:
        raise ValueError("Official FIFA ranking payload did not include PubDate")
    return {"latest_date": max(dates), "rankings": rankings}


def fetch_official_fifa_rankings() -> tuple[dict, str]:
    request = urllib.request.Request(
        FIFA_OFFICIAL_URL,
        headers={
            "Accept": "application/json",
            "Origin": "https://inside.fifa.com",
            "Referer": "https://inside.fifa.com/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    raw = urllib.request.urlopen(request, timeout=30).read().decode("utf-8")
    return parse_official_fifa_rankings(json.loads(raw)), raw


def parse_csv_fifa_rankings(csv_path: Path) -> tuple[dict, str]:
    if not csv_path.exists():
        raise FileNotFoundError(f"FIFA ranking CSV not found: {csv_path}")

    # Read CSV, group by date to compute ranks
    raw = csv_path.read_text(encoding="utf-8")
    by_date: dict[str, list[dict]] = defaultdict(list)
    reader = csv.DictReader(raw.splitlines())
    for row in reader:
        date = row.get("date", "").strip()
        short = row.get("team_short", "").strip()
        points = row.get("total_points", "").strip()
        if date and short and points:
            try:
                by_date[date].append({"short": short, "points": float(points)})
            except ValueError:
                pass

    # Sort dates, get latest
    dates = sorted(by_date.keys(), reverse=True)
    if not dates:
        raise ValueError("No dates found in FIFA ranking CSV")

    latest_date = dates[0]
    latest_entries = by_date[latest_date]
    # Sort by points descending to compute rank
    latest_entries.sort(key=lambda x: x["points"], reverse=True)
    latest_ranks = {}
    for i, entry in enumerate(latest_entries, start=1):
        latest_ranks[entry["short"]] = {"rank": i, "points": entry["points"]}

    return {"latest_date": latest_date, "rankings": latest_ranks}, raw


def populate_fifa_rankings(csv_path: Path, prefer_official: bool = True) -> dict:
    provider = FIFA_CSV_PROVIDER
    source_url = str(csv_path.relative_to(PROJECT_ROOT))
    if prefer_official:
        try:
            parsed, raw = fetch_official_fifa_rankings()
            provider = FIFA_OFFICIAL_PROVIDER
            source_url = FIFA_OFFICIAL_URL
        except Exception:
            parsed, raw = parse_csv_fifa_rankings(csv_path)
    else:
        parsed, raw = parse_csv_fifa_rankings(csv_path)

    latest_date = parsed["latest_date"]
    latest_ranks = parsed["rankings"]
    checksum = sha256(raw.encode()).hexdigest()
    source_label = f"world_football_elo+{provider}:{latest_date}"
    with session_scope() as session:
        teams = list(session.scalars(select(Team)))
        team_ids = {t.id for t in teams}

        updated = 0
        for team_id in team_ids:
            fifa_data = latest_ranks.get(team_id)
            if not fifa_data:
                continue

            rating = session.scalar(
                select(TeamRating)
                .where(TeamRating.team_id == team_id)
                .order_by(TeamRating.effective_date.desc())
                .limit(1)
            )
            if rating is None:
                continue

            if rating.fifa_rank != fifa_data["rank"] or rating.fifa_points != fifa_data["points"] or provider not in rating.source:
                rating.fifa_rank = fifa_data["rank"]
                rating.fifa_points = fifa_data["points"]
                rating.source = source_label
                updated += 1

        snapshot = session.scalar(
            select(DataSnapshot).where(
                DataSnapshot.provider == FIFA_CSV_PROVIDER,
                DataSnapshot.checksum == checksum,
            )
        )
        if snapshot is None:
            session.add(
                DataSnapshot(
                    provider=provider,
                    source_url=source_url,
                    fetched_at=datetime.fromisoformat(latest_date).replace(tzinfo=timezone.utc),
                    status="available",
                    checksum=checksum,
                    coverage={"teams": len(latest_ranks), "matched_world_cup_teams": updated},
                )
            )

    return {
        "latest_date": latest_date,
        "ranked_teams": len(latest_ranks),
        "updated": updated,
        "source": source_label,
        "provider": provider,
    }


def main():
    result = populate_fifa_rankings(PROJECT_ROOT / "data" / "external" / "fifa_ranking.csv")
    print(result)


if __name__ == "__main__":
    main()
