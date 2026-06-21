#!/usr/bin/env python3
"""下载 football-data.co.uk 的历史赔率数据（免费，无需 API key）"""
import urllib.request
import os
import time

DATA_DIR = "/Users/liudapeng/Documents/code/others/world_cup/data/external/odds"
os.makedirs(DATA_DIR, exist_ok=True)

BASE = "https://www.football-data.co.uk"

# 主要联赛 + 赛季映射
# 格式: (league_code, league_name, seasons)
# season: "9900" = 1999/00, "0001" = 2000/01, ..., "2425" = 2024/25
leagues = [
    ("E0", "england_premier_league"),
    ("E1", "england_championship"),
    ("D1", "germany_bundesliga1"),
    ("D2", "germany_bundesliga2"),
    ("SP1", "spain_laliga"),
    ("SP2", "spain_segunda"),
    ("I1", "italy_serieA"),
    ("I2", "italy_serieB"),
    ("F1", "france_ligue1"),
    ("F2", "france_ligue2"),
]

# 2010/11 到 2024/25
seasons = [f"{y}{y+1}" for y in range(10, 25)]  # 1011 to 2425

total = len(leagues) * len(seasons)
downloaded = 0
skipped = 0
failed = 0

for league_code, league_name in leagues:
    for season in seasons:
        fname = f"{DATA_DIR}/{league_name}_{season}.csv"
        if os.path.exists(fname) and os.path.getsize(fname) > 100:
            skipped += 1
            continue
        
        url = f"{BASE}/mmz4281/{season}/{league_code}.csv"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            if len(data) > 100:
                with open(fname, 'wb') as f:
                    f.write(data)
                downloaded += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
        
        time.sleep(0.1)  # rate limit
        
        if (downloaded + skipped + failed) % 20 == 0:
            print(f"  进度: {downloaded+skipped+failed}/{total} (下载={downloaded}, 跳过={skipped}, 失败={failed})")

print(f"\n完成! 下载={downloaded}, 跳过={skipped}, 失败={failed}")

# 统计
import pandas as pd
total_rows = 0
total_files = 0
for f in os.listdir(DATA_DIR):
    if f.endswith('.csv') and f != 'notes.txt':
        try:
            df = pd.read_csv(os.path.join(DATA_DIR, f), nrows=1)
            if 'B365H' in df.columns or 'BWH' in df.columns:
                total_files += 1
                df_full = pd.read_csv(os.path.join(DATA_DIR, f))
                total_rows += len(df_full)
        except:
            pass

print(f"含赔率数据的文件: {total_files}, 总比赛数: {total_rows}")
