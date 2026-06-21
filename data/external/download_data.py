#!/usr/bin/env python3
"""下载 StatsBomb 世界杯数据和 Open-Meteo 天气数据"""
import json, urllib.request, os, time, sys

BASE = 'https://raw.githubusercontent.com/statsbomb/open-data/master/data'
DATA_DIR = '/Users/liudapeng/Documents/code/others/world_cup/data/external/statsbomb'
os.makedirs(DATA_DIR, exist_ok=True)

def download(url, fname):
    if os.path.exists(fname):
        return True
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        with open(fname, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"  下载失败 {url}: {e}")
        return False

# 1. 下载比赛列表
for comp_id, season_id, label in [(43, 3, '2018'), (43, 106, '2022')]:
    url = f'{BASE}/matches/{comp_id}/{season_id}.json'
    fname = f'{DATA_DIR}/wc_{label}_matches.json'
    print(f'下载 {label} 世界杯比赛列表...')
    if download(url, fname):
        with open(fname) as f:
            matches = json.load(f)
        print(f'  {len(matches)} 场比赛')

# 2. 下载事件数据（含 xG）
for label in ['2018', '2022']:
    matches_file = f'{DATA_DIR}/wc_{label}_matches.json'
    with open(matches_file) as f:
        matches = json.load(f)
    
    events_dir = f'{DATA_DIR}/events_{label}'
    os.makedirs(events_dir, exist_ok=True)
    
    downloaded = 0
    skipped = 0
    for m in matches:
        mid = m['match_id']
        efile = f'{events_dir}/{mid}.json'
        if os.path.exists(efile):
            skipped += 1
            continue
        url = f'{BASE}/events/{mid}.json'
        if download(url, efile):
            downloaded += 1
            if downloaded % 10 == 0:
                print(f'  {label}: 已下载 {downloaded} 场...')
            time.sleep(0.3)
    
    print(f'{label} 世界杯: 新下载 {downloaded}, 已缓存 {skipped}')

# 3. 提取 xG 数据
print('\n提取 xG 数据...')
xg_data = {}
for label in ['2018', '2022']:
    matches_file = f'{DATA_DIR}/wc_{label}_matches.json'
    with open(matches_file) as f:
        matches = json.load(f)
    
    for m in matches:
        mid = m['match_id']
        home = m['home_team']['home_team_name']
        away = m['away_team']['away_team_name']
        date = m['match_date'][:10]
        
        # 从比赛元数据获取 xG
        home_xg = m.get('home_score', 0)  # fallback
        away_xg = m.get('away_score', 0)
        
        # 尝试从事件数据获取更精确的 xG
        efile = f'{DATA_DIR}/events_{label}/{mid}.json'
        if os.path.exists(efile):
            try:
                with open(efile) as f:
                    events = json.load(f)
                
                hxg = 0.0
                axg = 0.0
                for e in events:
                    if e.get('type', {}).get('name') == 'Shot':
                        shot = e.get('shot', {})
                        xg_val = shot.get('statsbomb_xg', 0)
                        team = e.get('team', {}).get('name', '')
                        if team == home:
                            hxg += xg_val
                        else:
                            axg += xg_val
                
                xg_data[str(mid)] = {
                    'date': date,
                    'home_team': home,
                    'away_team': away,
                    'home_xg': round(hxg, 3),
                    'away_xg': round(axg, 3),
                    'home_score': m.get('home_score', 0),
                    'away_score': m.get('away_score', 0),
                    'competition': 'World Cup',
                    'season': label,
                }
            except Exception as e:
                pass

# 保存 xG 数据
xg_file = f'{DATA_DIR}/world_cup_xg.json'
with open(xg_file, 'w') as f:
    json.dump(xg_data, f, indent=2)
print(f'xG 数据已保存: {len(xg_data)} 场比赛 -> {xg_file}')

# 4. 下载 Open-Meteo 天气数据
print('\n下载世界杯比赛天气数据...')
import sqlite3

# 世界杯主办城市坐标
WC_CITIES = {
    # 2018 俄罗斯
    'Moscow': (55.75, 37.62), 'St. Petersburg': (59.93, 30.32),
    'Sochi': (43.60, 39.73), 'Kazan': (55.79, 49.11),
    'Nizhny Novgorod': (56.33, 44.00), 'Samara': (53.20, 50.15),
    'Volgograd': (48.71, 44.51), 'Rostov-on-Don': (47.23, 39.72),
    'Yekaterinburg': (56.84, 60.61), 'Saransk': (54.18, 45.18),
    'Kaliningrad': (54.71, 20.46),
    # 2022 卡塔尔
    'Doha': (25.29, 51.53), 'Al Rayyan': (25.27, 51.42),
    'Lusail': (25.44, 51.50), 'Al Wakrah': (25.18, 51.60),
    'Al Khor': (25.68, 51.50),
}

# 从数据库获取世界杯比赛
db_path = '/Users/liudapeng/Documents/code/others/world_cup/backend/world_cup.db'
weather_data = {}

# 从历史数据 CSV 获取世界杯比赛
import pandas as pd
results = pd.read_csv('/Users/liudapeng/Documents/code/others/world_cup/data/external/results.csv')
wc_matches = results[results['tournament'].str.contains('FIFA World Cup', na=False)]
wc_matches = wc_matches[wc_matches['date'] >= '2010-01-01']
print(f'历史世界杯比赛: {len(wc_matches)} 场')

# 只对 2018 和 2022 下载天气（有城市信息）
wc_recent = wc_matches[wc_matches['date'] >= '2018-01-01']
print(f'2018+ 世界杯比赛: {len(wc_recent)} 场')

# 城市名映射
city_map = {}
for city in WC_CITIES:
    city_map[city.lower()] = city

weather_file = f'{DATA_DIR}/weather_data.json'
if os.path.exists(weather_file):
    with open(weather_file) as f:
        weather_data = json.load(f)
    print(f'天气数据已缓存: {len(weather_data)} 条')

# 对有城市信息的比赛下载天气
for idx, row in wc_recent.iterrows():
    key = f"{row['date']}_{row['home_team']}_{row['away_team']}"
    if key in weather_data:
        continue
    
    city = str(row.get('city', '')).strip()
    if not city:
        continue
    
    # 匹配城市坐标
    matched_city = None
    for lc, mc in city_map.items():
        if lc in city.lower() or city.lower() in lc:
            matched_city = mc
            break
    
    if not matched_city:
        continue
    
    lat, lon = WC_CITIES[matched_city]
    date = row['date']
    
    # Open-Meteo 历史天气 API
    url = (f'https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}'
           f'&start_date={date}&end_date={date}'
           f'&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation,weather_code'
           f'&timezone=auto')
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            wdata = json.loads(resp.read().decode())
        
        hourly = wdata.get('hourly', {})
        # 取比赛时段 12:00-22:00 UTC 的均值
        hours = hourly.get('time', [])
        temps = hourly.get('temperature_2m', [])
        humids = hourly.get('relative_humidity_2m', [])
        winds = hourly.get('wind_speed_10m', [])
        precips = hourly.get('precipitation', [])
        
        match_temps = [t for h, t in zip(hours, temps) if h and '12:00' <= h[-5:] <= '22:00' and t is not None]
        match_humids = [h for ht, h in zip(hours, humids) if ht and '12:00' <= ht[-5:] <= '22:00' and h is not None]
        match_winds = [w for h, w in zip(hours, winds) if h and '12:00' <= h[-5:] <= '22:00' and w is not None]
        match_precips = [p for h, p in zip(hours, precips) if h and '12:00' <= h[-5:] <= '22:00' and p is not None]
        
        if match_temps:
            weather_data[key] = {
                'date': date,
                'city': matched_city,
                'temperature': round(sum(match_temps)/len(match_temps), 1),
                'humidity': round(sum(match_humids)/len(match_humids), 1) if match_humids else None,
                'wind_speed': round(sum(match_winds)/len(match_winds), 1) if match_winds else None,
                'precipitation': round(sum(match_precips)/len(match_precips), 2) if match_precips else None,
            }
            time.sleep(0.1)
    except Exception as e:
        pass

# 保存天气数据
with open(weather_file, 'w') as f:
    json.dump(weather_data, f, indent=2)
print(f'天气数据已保存: {len(weather_data)} 条 -> {weather_file}')

print('\n所有数据下载完成!')
