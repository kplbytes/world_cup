#!/usr/bin/env python3
"""Check tomorrow's matches and trigger full prediction workflow."""
import json, urllib.request
from datetime import datetime, timedelta, timezone

resp = urllib.request.urlopen("http://localhost:8000/api/dashboard")
d = json.loads(resp.read())

tz = timezone(timedelta(hours=8))
tomorrow = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
today = datetime.now(tz).strftime("%Y-%m-%d")

print(f"Today: {today}, Tomorrow: {tomorrow}")
print()

upcoming = []
for group in d.get('groups', []):
    for m in group.get('matches', []):
        kickoff = m.get('kickoff', '')
        if kickoff and (tomorrow in kickoff or today in kickoff):
            if m.get('status') != 'final':
                upcoming.append(m)

print(f"Upcoming matches (today+tomorrow): {len(upcoming)}")
for m in upcoming:
    home = m['home_team']['id']
    away = m['away_team']['id']
    kickoff = m.get('kickoff', '?')
    has_pred = 'prediction' in m and m['prediction'] is not None
    has_ai = m.get('ai_prediction') is not None
    print(f"  {home} vs {away} | {kickoff[11:16]} | pred={has_pred} ai={has_ai}")
