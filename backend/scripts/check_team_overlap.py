import sqlite3
conn = sqlite3.connect('world_cup.db')
c = conn.cursor()
c.execute('SELECT id FROM teams')
team_ids = {r[0] for r in c.fetchall()}
c.execute('SELECT DISTINCT home_team_id FROM historical_matches WHERE home_team_id IS NOT NULL')
home_ids = {r[0] for r in c.fetchall()}
c.execute('SELECT DISTINCT away_team_id FROM historical_matches WHERE away_team_id IS NOT NULL')
away_ids = {r[0] for r in c.fetchall()}
hist_ids = home_ids | away_ids
overlap = team_ids & hist_ids
print(f'Teams: {len(team_ids)}, Hist: {len(hist_ids)}, Overlap: {len(overlap)}')
print(f'Team IDs: {sorted(team_ids)[:5]}')
print(f'Hist IDs: {sorted(hist_ids)[:5]}')
print(f'Overlap: {sorted(overlap)[:5]}')
# Count matches with both teams in overlap
placeholders = ','.join('?' for _ in overlap)
overlap_list = sorted(overlap)
c.execute(
    f'SELECT COUNT(*) FROM historical_matches WHERE home_team_id IN ({placeholders}) AND away_team_id IN ({placeholders}) AND score_scope="full_90min"',
    overlap_list + overlap_list
)
print(f'Matches with both teams in teams table: {c.fetchone()[0]}')
conn.close()
