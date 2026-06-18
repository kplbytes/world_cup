#!/usr/bin/env python3
"""Verify dashboard API and update-predictions."""
import json
from fastapi.testclient import TestClient
from app.db import create_database
from app.main import create_app

create_database()
app = create_app(start_background=False)
client = TestClient(app)

# 1. Dashboard
print("=== /api/dashboard ===")
resp = client.get("/api/dashboard")
data = resp.json()
print(f"current_time_china: {data.get('current_time_china')}")
print(f"last_updated: {data.get('last_updated')}")
print(f"data_age_minutes: {data.get('data_age_minutes')}")
nm = data.get("next_match")
if nm:
    print(f"next_match: {nm.get('home_team', {}).get('name')} vs {nm.get('away_team', {}).get('name')}")
    print(f"next_match.status: {nm.get('status')}")
else:
    print("next_match: None")

# Finished matches review
finished = data.get("finished_matches", [])
print(f"finished_matches count: {len(finished)}")
for m in finished[:3]:
    review = m.get("match_review", {})
    print(f"  {m.get('home_team', {}).get('name')} vs {m.get('away_team', {}).get('name')}: scoring_status={review.get('scoring_status', 'N/A')}")

# 2. Match count breakdown
print()
print("=== /api/match-count-breakdown ===")
resp = client.get("/api/match-count-breakdown")
print(json.dumps(resp.json(), indent=2))

# 3. Profile coverage
print()
print("=== /api/team-profiles/coverage ===")
resp = client.get("/api/team-profiles/coverage")
print(json.dumps(resp.json(), indent=2))

# 4. Update predictions
print()
print("=== POST /api/workflows/update-predictions ===")
resp = client.post("/api/workflows/update-predictions")
data = resp.json()
print(f"status: {data.get('status')}")
print(f"future_scheduled_count: {data.get('future_scheduled_count')}")
print(f"scoring_eligible_future: {data.get('scoring_eligible_future')}")
print(f"snapshots_created: {data.get('snapshots_created')}")
print(f"snapshots_locked: {data.get('snapshots_locked')}")
print(f"real_profiles_used: {data.get('real_profiles_used')}")
print(f"mock_profiles_blocked: {data.get('mock_profiles_blocked')}")
print(f"profile_version: {data.get('profile_version')}")
