#!/usr/bin/env python3
"""Show tomorrow's match predictions with all factors."""
import json, urllib.request
from datetime import datetime, timedelta, timezone

resp = urllib.request.urlopen("http://localhost:8000/api/dashboard")
d = json.loads(resp.read())

tz = timezone(timedelta(hours=8))
tomorrow = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")
today = datetime.now(tz).strftime("%Y-%m-%d")

print(f"=== 今日+明日比赛预测 ({today} / {tomorrow}) ===\n")

for group in d.get('groups', []):
    for m in group.get('matches', []):
        kickoff = m.get('kickoff', '')
        if not kickoff or (tomorrow not in kickoff and today not in kickoff):
            continue
        if m.get('status') == 'final':
            continue

        home = m['home_team']
        away = m['away_team']
        p = m.get('prediction')
        ai = m.get('ai_prediction')
        ens = m.get('ensemble_prediction')

        home_name = home.get('short_name', home['id'])
        away_name = away.get('short_name', away['id'])
        time_str = kickoff[11:16] if len(kickoff) > 16 else '?'

        print(f"⚽ {home_name} vs {away_name}  ({time_str})")
        print(f"   FIFA排名: #{home.get('fifa_rank','?')} vs #{away.get('fifa_rank','?')}")

        if p:
            mi = p.get('model_inputs', {})
            print(f"   系统预测: 主{p['home_win']:.1%} 平{p['draw']:.1%} 客{p['away_win']:.1%}")
            if mi.get('fifa_rank_delta') is not None:
                print(f"   因子: FIFAΔ={mi['fifa_rank_delta']} Elo近={mi.get('elo_closeness',0):.2f} 小组赛={mi.get('is_group_stage',True)}")

        if ai:
            print(f"   AI预测:   主{ai['home_win']:.1%} 平{ai['draw']:.1%} 客{ai['away_win']:.1%}")

        if ens:
            print(f"   最终Ensemble: 主{ens['home_win']:.1%} 平{ens['draw']:.1%} 客{ens['away_win']:.1%}")
            rec = ens.get('recommendation', {})
            if rec:
                print(f"   推荐: {rec.get('direction','?')} (置信度{rec.get('confidence','?')})")

        print()
