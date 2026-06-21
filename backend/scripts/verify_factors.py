#!/usr/bin/env python3
"""Verify FIFA rank factor is working in predictions."""
import sys, json, urllib.request

resp = urllib.request.urlopen("http://localhost:8000/api/dashboard")
d = json.loads(resp.read())

count = 0
for group in d.get('groups', []):
    for m in group.get('matches', []):
        if m.get('status') != 'final' and m.get('prediction') and count < 5:
            p = m['prediction']
            mi = p.get('model_inputs', {})
            home_id = m['home_team']['id']
            away_id = m['away_team']['id']
            frd = mi.get('fifa_rank_delta', 'N/A')
            ec = mi.get('elo_closeness', 'N/A')
            fra = mi.get('fifa_rank_adjustment', 'N/A')
            print(f'{home_id} vs {away_id}: draw={p["draw"]:.3f} fifa_delta={frd} elo_close={ec} adj={fra}')
            count += 1
