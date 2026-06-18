import sqlite3, datetime
conn = sqlite3.connect('world_cup.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT COUNT(DISTINCT m.id) as cnt
    FROM matches m
    WHERE m.status != 'final'
    AND m.kickoff >= datetime('now')
    AND m.id IN (SELECT DISTINCT match_id FROM prediction_snapshots)
""")
print('Upcoming matches WITH snapshot:', cur.fetchone()['cnt'])

cur.execute("""
    SELECT COUNT(DISTINCT m.id) as cnt
    FROM matches m
    WHERE m.status != 'final'
    AND m.kickoff >= datetime('now')
    AND m.id NOT IN (SELECT DISTINCT match_id FROM prediction_snapshots)
""")
print('Upcoming matches WITHOUT snapshot:', cur.fetchone()['cnt'])

cur.execute("""
    SELECT m.id, m.home_team_id, m.away_team_id, m.kickoff, m.status,
           (SELECT COUNT(*) FROM prediction_snapshots ps WHERE ps.match_id = m.id) as snap_count
    FROM matches m
    WHERE m.status != 'final'
    AND m.kickoff >= datetime('now')
    ORDER BY m.kickoff
    LIMIT 15
""")
print('\n=== Upcoming matches sample ===')
for row in cur.fetchall():
    print(f'  {row["id"]} | {row["home_team_id"]} vs {row["away_team_id"]} | {row["kickoff"]} | snaps={row["snap_count"]}')

now_utc = datetime.datetime.now(datetime.timezone.utc)
now_beijing = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
print(f'\nNow UTC: {now_utc.strftime("%Y-%m-%d %H:%M:%S")}')
print(f'Now Beijing: {now_beijing.strftime("%Y-%m-%d %H:%M:%S")}')
cur.execute("SELECT datetime('now')")
print(f'SQLite now(): {cur.fetchone()[0]}')

cur.execute("SELECT COUNT(*) as cnt FROM matches WHERE home_team_id IS NULL OR away_team_id IS NULL")
print(f'\nTBD matches: {cur.fetchone()["cnt"]}')

cur.execute("""
    SELECT COUNT(DISTINCT e.match_id) as cnt
    FROM ensemble_predictions e
    JOIN matches m ON e.match_id = m.id
    WHERE m.status != 'final' AND m.kickoff >= datetime('now')
""")
print(f'Upcoming matches WITH ensemble: {cur.fetchone()["cnt"]}')

conn.close()
