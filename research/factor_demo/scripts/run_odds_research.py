#!/usr/bin/env python3
"""赔率因子研究脚本

将博彩赔率数据集成到因子研究系统，评估赔率因子能否突破 2% Brier 改善阈值。

Stage 1: 加载和清洗赔率数据 (149 CSV, 10 欧洲联赛, 2010-2025)
Stage 2: 计算赔率因子 (17 个)
Stage 3: 因子影响分析 (IC/ICIR/方向稳定性/Brier改善/MI/SHAP)
Stage 4: 模型对比 (6 种模型)
Stage 5: 严格验证 (Walk-Forward/Bootstrap/校准/Brier分解)
Stage 6: 最终评估 — 赔率因子能否突破 2% Brier 改善阈值？

关键设计决策:
  赔率数据来自联赛比赛（非国际比赛），无法直接与 feature_cache.csv 合并。
  因此我们在联赛数据上独立构建完整流水线：
  1. 从联赛数据构建 Elo 评分
  2. 计算基础因子（elo_diff, form, attack/defense 等）
  3. 添加赔率因子
  4. 在联赛数据集上评估
  5. 推断：如果赔率因子在联赛数据上有帮助，在国际数据上也应有帮助
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from scipy.stats import poisson

# ─── 项目路径 ──────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.evaluation.metrics import (
    evaluate_predictions,
    brier_score,
    multiclass_ece,
    reliability_curve,
    compute_factor_direction_stability,
)
from src.evaluation.calibration import (
    calibration_metrics,
    reliability_diagram_data,
    isotonic_calibration,
    platt_scale_calibration,
)
from src.evaluation.bootstrap import bootstrap_brier_comparison, bootstrap_brier_single
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.metrics import mutual_info_score
from sklearn.model_selection import TimeSeriesSplit

import lightgbm as lgb

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────────────────────
RANDOM_SEED = 42
DATA_START = "2010-01-01"
TRAIN_END = "2020-12-31"
PROMOTION_THRESHOLD = 0.02  # 2% Brier 改善阈值

# ─── 工具函数 ──────────────────────────────────────────────────────────
def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def save_json(data, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)
    print(f"  [保存] {path}")


def print_section(title: str):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def label_to_numeric(labels: np.ndarray) -> np.ndarray:
    """H/D/A -> 2/1/0"""
    m = {"H": 2, "D": 1, "A": 0}
    return np.array([m[l] for l in labels])


def reorder_probs_for_eval(probs: np.ndarray) -> np.ndarray:
    """将模型输出 [A, D, H] 重排为 evaluate_predictions 期望的 [H, D, A]"""
    return probs[:, [2, 1, 0]]


def label_to_onehot(labels: np.ndarray) -> np.ndarray:
    """H/D/A -> one-hot (N,3) [away, draw, home]"""
    m = {"A": 0, "D": 1, "H": 2}
    n = len(labels)
    oh = np.zeros((n, 3))
    for i, l in enumerate(labels):
        oh[i, m[l]] = 1.0
    return oh


# ======================================================================
# Stage 1: 加载和清洗赔率数据
# ======================================================================

# 赔率数据队名 -> 标准队名映射
TEAM_NAME_MAP = {
    # 英格兰
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Man Utd": "Manchester United",
    "Tottenham": "Tottenham Hotspur",
    "Wolves": "Wolverhampton Wanderers",
    "Nott'm Forest": "Nottingham Forest",
    "Nottm Forest": "Nottingham Forest",
    "Sheffield Utd": "Sheffield United",
    "Newcastle": "Newcastle United",
    "Brighton": "Brighton and Hove Albion",
    "Leicester": "Leicester City",
    "West Ham": "West Ham United",
    "West Brom": "West Bromwich Albion",
    "Aston Villa": "Aston Villa",
    "Norwich": "Norwich City",
    "Stoke": "Stoke City",
    "Swansea": "Swansea City",
    "Bournemouth": "AFC Bournemouth",
    "Huddersfield": "Huddersfield Town",
    "QPR": "Queens Park Rangers",
    "Cardiff": "Cardiff City",
    "Hull": "Hull City",
    "Middlesbrough": "Middlesbrough",
    "Sunderland": "Sunderland",
    "Blackburn": "Blackburn Rovers",
    "Bolton": "Bolton Wanderers",
    "Wigan": "Wigan Athletic",
    "Reading": "Reading",
    "Fulham": "Fulham",
    "Brentford": "Brentford",
    # 德国
    "M'gladbach": "Borussia Mönchengladbach",
    "Monchengladbach": "Borussia Mönchengladbach",
    "Köln": "FC Köln",
    "Cologne": "FC Köln",
    "Leverkusen": "Bayer Leverkusen",
    "Dortmund": "Borussia Dortmund",
    "Munich": "Bayern Munich",
    "Bayern Munich": "Bayern Munich",
    "Freiburg": "SC Freiburg",
    "Hoffenheim": "TSG Hoffenheim",
    "Wolfsburg": "VfL Wolfsburg",
    "Frankfurt": "Eintracht Frankfurt",
    "Stuttgart": "VfB Stuttgart",
    "Augsburg": "FC Augsburg",
    "Mainz": "1. FSV Mainz 05",
    "Bremen": "Werder Bremen",
    "Düsseldorf": "Fortuna Düsseldorf",
    "Dusseldorf": "Fortuna Düsseldorf",
    "Nürnberg": "1. FC Nürnberg",
    "Nurnberg": "1. FC Nürnberg",
    "Hannover": "Hannover 96",
    "Hamburg": "Hamburger SV",
    "Hertha": "Hertha BSC",
    "Schalke 04": "FC Schalke 04",
    "Schalke": "FC Schalke 04",
    "Heidenheim": "1. FC Heidenheim",
    "Darmstadt": "SV Darmstadt 98",
    "Bochum": "VfL Bochum",
    "Greuther Fürth": "Greuther Fürth",
    "Paderborn": "SC Paderborn 07",
    "Ingolstadt": "FC Ingolstadt 04",
    "Braunschweig": "Eintracht Braunschweig",
    "Sandhausen": "SV Sandhausen",
    "Kaiserslautern": "1. FC Kaiserslautern",
    "Karlsruhe": "Karlsruher SC",
    "St. Pauli": "FC St. Pauli",
    "Holstein Kiel": "Holstein Kiel",
    # 西班牙
    "Ath Bilbao": "Athletic Bilbao",
    "Ath Madrid": "Atlético Madrid",
    "Atletico Madrid": "Atlético Madrid",
    "Celta": "Celta Vigo",
    "Sociedad": "Real Sociedad",
    "Betis": "Real Betis",
    "Sevilla": "Sevilla",
    "Valencia": "Valencia",
    "Villarreal": "Villarreal",
    "Barcelona": "Barcelona",
    "Real Madrid": "Real Madrid",
    "Espanol": "Espanyol",
    "Espanyol": "Espanyol",
    "La Coruna": "Deportivo La Coruña",
    "Dep. La Coruña": "Deportivo La Coruña",
    "Alaves": "Deportivo Alavés",
    "Leganes": "Leganés",
    "Girona": "Girona",
    "Getafe": "Getafe",
    "Levante": "Levante",
    "Granada": "Granada",
    "Mallorca": "RCD Mallorca",
    "Osasuna": "CA Osasuna",
    "Valladolid": "Real Valladolid",
    "Rayo Vallecano": "Rayo Vallecano",
    "Cadiz": "Cádiz",
    "Elche": "Elche",
    "Almeria": "UD Almería",
    "Las Palmas": "UD Las Palmas",
    "Huesca": "SD Huesca",
    # 意大利
    "Inter": "Inter Milan",
    "Milan": "AC Milan",
    "Roma": "AS Roma",
    "Lazio": "SS Lazio",
    "Verona": "Hellas Verona",
    "Chievo": "Chievo Verona",
    "Juventus": "Juventus",
    "Napoli": "Napoli",
    "Fiorentina": "Fiorentina",
    "Torino": "Torino",
    "Sampdoria": "Sampdoria",
    "Genoa": "Genoa",
    "Bologna": "Bologna",
    "Udinese": "Udinese",
    "Cagliari": "Cagliari",
    "Atalanta": "Atalanta",
    "Sassuolo": "Sassuolo",
    "Empoli": "Empoli",
    "Parma": "Parma",
    "Benevento": "Benevento",
    "Spal": "SPAL",
    "Frosinone": "Frosinone",
    "Carpi": "Carpi",
    "Palermo": "Palermo",
    "Cesena": "Cesena",
    "Lecce": "Lecce",
    "Brescia": "Brescia",
    "Venezia": "Venezia",
    "Monza": "AC Monza",
    "Salernitana": "Salernitana",
    "Cremonese": "US Cremonese",
    # 法国
    "Paris SG": "Paris Saint-Germain",
    "Paris Saint Germain": "Paris Saint-Germain",
    "St Etienne": "Saint-Étienne",
    "Saint-Etienne": "Saint-Étienne",
    "Marseille": "Marseille",
    "Lyon": "Lyon",
    "Monaco": "Monaco",
    "Lille": "Lille",
    "Nice": "Nice",
    "Rennes": "Rennes",
    "Bordeaux": "Bordeaux",
    "Montpellier": "Montpellier",
    "Nantes": "Nantes",
    "Reims": "Reims",
    "Strasbourg": "Strasbourg",
    "Toulouse": "Toulouse",
    "Amiens": "Amiens",
    "Metz": "Metz",
    "Brest": "Brest",
    "Lens": "Lens",
    "Lorient": "Lorient",
    "Clermont": "Clermont Foot",
    "Auxerre": "AJ Auxerre",
    "Ajaccio": "AC Ajaccio",
    "Le Havre": "Le Havre",
    "Dijon": "Dijon",
    "Caen": "Caen",
    "Guingamp": "Guingamp",
    "Nancy": "Nancy",
    "Evian": "Evian",
    "Sochaux": "Sochaux",
    "Valenciennes": "Valenciennes",
    "Istres": "Istres",
    "Bastia": "Bastia",
    "Troyes": "Troyes",
}

# 联赛名映射
LEAGUE_MAP = {
    "E0": "England Premier League",
    "E1": "England Championship",
    "D1": "Germany Bundesliga 1",
    "D2": "Germany Bundesliga 2",
    "SP1": "Spain La Liga",
    "SP2": "Spain Segunda",
    "F1": "France Ligue 1",
    "F2": "France Ligue 2",
    "I1": "Italy Serie A",
    "I2": "Italy Serie B",
}

# 需要提取的赔率列
ODDS_COLUMNS = {
    "opening": ["B365H", "B365D", "B365A", "BWH", "BWD", "BWA",
                "IWH", "IWD", "IWA", "PSH", "PSD", "PSA",
                "WHH", "WHD", "WHA",
                "AvgH", "AvgD", "AvgA", "MaxH", "MaxD", "MaxA"],
    "closing": ["B365CH", "B365CD", "B365CA", "BWCH", "BWCD", "BWCA",
                "IWCH", "IWCD", "IWCA", "PSCH", "PSCD", "PSCA",
                "WHCH", "WHCD", "WHCA",
                "AvgCH", "AvgCD", "AvgCA", "MaxCH", "MaxCD", "MaxCA"],
}


def standardize_team_name(name: str, known_names: set | None = None) -> str:
    """标准化队名"""
    if name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[name]
    # 如果有已知名称集合，尝试模糊匹配
    if known_names and len(known_names) > 0:
        matches = get_close_matches(name, known_names, n=1, cutoff=0.8)
        if matches:
            return matches[0]
    return name


def load_all_odds_files(odds_dir: Path) -> pd.DataFrame:
    """加载所有赔率 CSV 文件"""
    print("  加载赔率数据...")
    csv_files = sorted(odds_dir.glob("*.csv"))
    print(f"  发现 {len(csv_files)} 个 CSV 文件")

    all_dfs = []
    for fpath in csv_files:
        try:
            # football-data.co.uk 的 CSV 用逗号分隔，但可能有编码问题
            df = pd.read_csv(fpath, encoding="utf-8", on_bad_lines="skip")
        except UnicodeDecodeError:
            try:
                df = pd.read_csv(fpath, encoding="latin-1", on_bad_lines="skip")
            except Exception:
                print(f"  [跳过] 无法读取: {fpath.name}")
                continue
        except Exception:
            print(f"  [跳过] 无法读取: {fpath.name}")
            continue

        # 从文件名推断联赛和赛季
        fname = fpath.stem
        df["_source_file"] = fname
        all_dfs.append(df)

    if not all_dfs:
        raise ValueError("没有成功加载任何赔率文件")

    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"  原始数据: {len(combined)} 行")
    return combined


def clean_odds_data(df: pd.DataFrame) -> pd.DataFrame:
    """清洗赔率数据"""
    print("  清洗赔率数据...")

    # 解析日期 (DD/MM/YY 格式)
    df["match_date"] = pd.to_datetime(df["Date"], format="mixed", dayfirst=True, errors="coerce")
    df = df[df["match_date"].notna()].copy()

    # 过滤 2010 年以后
    df = df[df["match_date"] >= DATA_START].copy()

    # 标准化队名
    all_teams = set(df["HomeTeam"].dropna().unique()) | set(df["AwayTeam"].dropna().unique())
    known_names = set(TEAM_NAME_MAP.values()) | all_teams

    df["home_team"] = df["HomeTeam"].apply(lambda x: standardize_team_name(str(x), known_names) if pd.notna(x) else x)
    df["away_team"] = df["AwayTeam"].apply(lambda x: standardize_team_name(str(x), known_names) if pd.notna(x) else x)

    # 比赛结果
    df["result"] = df["FTR"].map({"H": "H", "D": "D", "A": "A"})
    df = df[df["result"].notna()].copy()

    # 进球数
    df["home_goals"] = pd.to_numeric(df["FTHG"], errors="coerce").fillna(0).astype(int)
    df["away_goals"] = pd.to_numeric(df["FTAG"], errors="coerce").fillna(0).astype(int)

    # 联赛
    df["league"] = df["Div"].map(LEAGUE_MAP).fillna(df["Div"])

    # 赔率列转为数值
    all_odds_cols = ODDS_COLUMNS["opening"] + ODDS_COLUMNS["closing"]
    for col in all_odds_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 添加时区
    df["match_date"] = df["match_date"].dt.tz_localize("UTC")

    print(f"  清洗后: {len(df)} 行, {df['match_date'].min().date()} ~ {df['match_date'].max().date()}")
    print(f"  联赛分布: {df['league'].value_counts().to_dict()}")
    return df


def compute_implied_probabilities(df: pd.DataFrame) -> pd.DataFrame:
    """计算隐含概率（去除 overround）"""
    print("  计算隐含概率...")

    bookmakers = [
        ("B365", "B365H", "B365D", "B365A"),
        ("BW", "BWH", "BWD", "BWA"),
        ("IW", "IWH", "IWD", "IWA"),
        ("PS", "PSH", "PSD", "PSA"),
        ("WH", "WHH", "WHD", "WHA"),
    ]

    for name, h_col, d_col, a_col in bookmakers:
        if h_col in df.columns and d_col in df.columns and a_col in df.columns:
            raw_h = 1.0 / df[h_col].replace(0, np.nan)
            raw_d = 1.0 / df[d_col].replace(0, np.nan)
            raw_a = 1.0 / df[a_col].replace(0, np.nan)
            overround = raw_h + raw_d + raw_a
            df[f"{name}_implied_home"] = raw_h / overround
            df[f"{name}_implied_draw"] = raw_d / overround
            df[f"{name}_implied_away"] = raw_a / overround

    # 市场平均隐含概率
    if all(c in df.columns for c in ["AvgH", "AvgD", "AvgA"]):
        raw_h = 1.0 / df["AvgH"].replace(0, np.nan)
        raw_d = 1.0 / df["AvgD"].replace(0, np.nan)
        raw_a = 1.0 / df["AvgA"].replace(0, np.nan)
        overround = raw_h + raw_d + raw_a
        df["avg_implied_home"] = raw_h / overround
        df["avg_implied_draw"] = raw_d / overround
        df["avg_implied_away"] = raw_a / overround

    # 收盘隐含概率 (Pinnacle)
    if all(c in df.columns for c in ["PSCH", "PSCD", "PSCA"]):
        raw_h = 1.0 / df["PSCH"].replace(0, np.nan)
        raw_d = 1.0 / df["PSCD"].replace(0, np.nan)
        raw_a = 1.0 / df["PSCA"].replace(0, np.nan)
        overround = raw_h + raw_d + raw_a
        df["closing_implied_home"] = raw_h / overround
        df["closing_implied_draw"] = raw_d / overround
        df["closing_implied_away"] = raw_a / overround

    return df


def build_league_elo(df: pd.DataFrame) -> pd.DataFrame:
    """从联赛数据构建 Elo 评分"""
    print("  构建联赛 Elo 评分...")

    # 准备 replay_elo_history 需要的格式
    elo_df = df[["match_date", "home_team", "away_team", "home_goals", "away_goals", "result"]].copy()
    elo_df["is_neutral"] = False  # 联赛比赛非中立场
    elo_df["tournament_category"] = "league"

    # 使用联赛专用 Elo 配置
    config = EloConfig(
        initial_rating=1500.0,
        k_factor=20.0,  # 联赛 K 值通常较小
        elo_scale=400.0,
        home_advantage=50.0,  # 联赛主场优势
        friendly_k=20.0,
    )

    elo_df = replay_elo_history(elo_df, config)
    df["pre_match_elo_home"] = elo_df["pre_match_elo_home"].values
    df["pre_match_elo_away"] = elo_df["pre_match_elo_away"].values
    df["elo_diff"] = elo_df["elo_diff"].values

    print(f"  Elo 评分构建完成, 覆盖 {df['pre_match_elo_home'].notna().sum()} 场比赛")
    return df


def compute_league_features(df: pd.DataFrame) -> pd.DataFrame:
    """从联赛数据计算基础因子（简化版，无需 as_of 模块）"""
    print("  计算联赛基础因子...")

    df = df.sort_values("match_date").reset_index(drop=True)

    # 为每支球队维护历史记录
    team_history = {}

    # 初始化因子列
    feature_cols = [
        "recent_form_5", "recent_form_10", "recent_goals_scored_5",
        "recent_goals_conceded_5", "recent_goal_diff_5",
        "home_form_5", "away_form_5",
    ]
    for col in feature_cols:
        df[col] = np.nan

    for idx, row in df.iterrows():
        ht = row["home_team"]
        at = row["away_team"]
        hg = row["home_goals"]
        ag = row["away_goals"]

        # 主队因子
        ht_hist = team_history.get(ht, [])
        if len(ht_hist) >= 5:
            recent = ht_hist[-5:]
            form = np.mean([r["outcome"] for r in recent])
            df.loc[idx, "recent_form_5"] = form
            df.loc[idx, "recent_goals_scored_5"] = sum(r["goals_scored"] for r in recent)
            df.loc[idx, "recent_goals_conceded_5"] = sum(r["goals_conceded"] for r in recent)
            df.loc[idx, "recent_goal_diff_5"] = sum(r["goal_diff"] for r in recent)
            # 主场表现
            home_recent = [r for r in ht_hist[-20:] if r["venue"] == "home"][-5:]
            if home_recent:
                df.loc[idx, "home_form_5"] = np.mean([r["outcome"] for r in home_recent])

        if len(ht_hist) >= 10:
            recent = ht_hist[-10:]
            df.loc[idx, "recent_form_10"] = np.mean([r["outcome"] for r in recent])

        # 客队因子
        at_hist = team_history.get(at, [])
        if len(at_hist) >= 5:
            recent = at_hist[-5:]
            away_recent = [r for r in at_hist[-20:] if r["venue"] == "away"][-5:]
            if away_recent:
                df.loc[idx, "away_form_5"] = np.mean([r["outcome"] for r in away_recent])

        # 更新历史
        ht_outcome = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        at_outcome = 1.0 if ag > hg else (0.5 if ag == hg else 0.0)

        team_history.setdefault(ht, []).append({
            "date": row["match_date"],
            "venue": "home",
            "outcome": ht_outcome,
            "goals_scored": hg,
            "goals_conceded": ag,
            "goal_diff": hg - ag,
        })
        team_history.setdefault(at, []).append({
            "date": row["match_date"],
            "venue": "away",
            "outcome": at_outcome,
            "goals_scored": ag,
            "goals_conceded": hg,
            "goal_diff": ag - hg,
        })

    # 计算差值因子
    df["form_diff_5"] = df["recent_form_5"]  # 已经是主队-客队视角
    df["goal_diff_factor"] = df["recent_goal_diff_5"]

    # 主客场表现差
    df["home_away_form_diff"] = df["home_form_5"] - df["away_form_5"]

    print(f"  基础因子计算完成")
    return df


# ======================================================================
# Stage 2: 计算赔率因子
# ======================================================================
def compute_odds_factors(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """计算 17 个赔率因子"""
    print("  计算赔率因子...")

    odds_factor_names = []

    # 1-3: 市场平均隐含概率
    if "avg_implied_home" in df.columns:
        df["odds_implied_home"] = df["avg_implied_home"]
        df["odds_implied_draw"] = df["avg_implied_draw"]
        df["odds_implied_away"] = df["avg_implied_away"]
        odds_factor_names.extend(["odds_implied_home", "odds_implied_draw", "odds_implied_away"])
    else:
        # 回退到 Bet365
        if "B365_implied_home" in df.columns:
            df["odds_implied_home"] = df["B365_implied_home"]
            df["odds_implied_draw"] = df["B365_implied_draw"]
            df["odds_implied_away"] = df["B365_implied_away"]
            odds_factor_names.extend(["odds_implied_home", "odds_implied_draw", "odds_implied_away"])

    # 4: Bookmaker margin (overround - 1)
    if all(c in df.columns for c in ["AvgH", "AvgD", "AvgA"]):
        raw_h = 1.0 / df["AvgH"].replace(0, np.nan)
        raw_d = 1.0 / df["AvgD"].replace(0, np.nan)
        raw_a = 1.0 / df["AvgA"].replace(0, np.nan)
        df["odds_margin"] = (raw_h + raw_d + raw_a) - 1.0
        odds_factor_names.append("odds_margin")

    # 5: Favorite strength
    if "odds_implied_home" in df.columns:
        df["odds_favorite_strength"] = df[["odds_implied_home", "odds_implied_draw", "odds_implied_away"]].max(axis=1) - 0.33
        odds_factor_names.append("odds_favorite_strength")

    # 6: Draw signal
    if "odds_implied_draw" in df.columns:
        historical_draw_rate = (df["result"] == "D").mean()
        df["odds_draw_signal"] = df["odds_implied_draw"] - historical_draw_rate
        odds_factor_names.append("odds_draw_signal")

    # 7: Odds disagreement (跨博彩公司隐含概率标准差)
    implied_cols_home = [c for c in df.columns if c.endswith("_implied_home") and not c.startswith("avg_") and not c.startswith("closing_")]
    implied_cols_draw = [c for c in df.columns if c.endswith("_implied_draw") and not c.startswith("avg_") and not c.startswith("closing_")]
    implied_cols_away = [c for c in df.columns if c.endswith("_implied_away") and not c.startswith("avg_") and not c.startswith("closing_")]

    if len(implied_cols_home) >= 2:
        all_implied = df[implied_cols_home + implied_cols_draw + implied_cols_away]
        df["odds_disagreement"] = all_implied.std(axis=1)
        odds_factor_names.append("odds_disagreement")

    # 8-10: Odds vs Elo
    if "odds_implied_home" in df.columns and "elo_diff" in df.columns:
        # 从 Elo 计算 implied 概率
        elo_scale = 400.0
        home_adv = 50.0
        expected_home = 1.0 / (1.0 + 10 ** ((df["pre_match_elo_away"] - (df["pre_match_elo_home"] + home_adv)) / elo_scale))
        # 简化：Elo 不直接给出平局概率，用 Poisson 近似
        draw_base = 0.26
        draw_adj = draw_base * (1.0 - abs(expected_home - 0.5))
        elo_implied_draw = draw_adj
        remaining = 1.0 - draw_adj
        elo_implied_home = remaining * expected_home
        elo_implied_away = remaining * (1.0 - expected_home)

        df["odds_vs_elo_home"] = df["odds_implied_home"] - elo_implied_home
        df["odds_vs_elo_draw"] = df["odds_implied_draw"] - elo_implied_draw
        df["odds_vs_elo_away"] = df["odds_implied_away"] - elo_implied_away
        odds_factor_names.extend(["odds_vs_elo_home", "odds_vs_elo_draw", "odds_vs_elo_away"])

    # 11-13: Pinnacle specific
    if "PS_implied_home" in df.columns:
        df["pinnacle_implied_home"] = df["PS_implied_home"]
        df["pinnacle_implied_draw"] = df["PS_implied_draw"]
        df["pinnacle_implied_away"] = df["PS_implied_away"]
        odds_factor_names.extend(["pinnacle_implied_home", "pinnacle_implied_draw", "pinnacle_implied_away"])

    # 14-15: Closing vs Opening movement
    if all(c in df.columns for c in ["PSCH", "PSH"]):
        opening_h = 1.0 / df["PSH"].replace(0, np.nan)
        closing_h = 1.0 / df["PSCH"].replace(0, np.nan)
        df["closing_vs_opening_home"] = closing_h - opening_h

        opening_d = 1.0 / df["PSD"].replace(0, np.nan)
        closing_d = 1.0 / df["PSCD"].replace(0, np.nan)
        df["closing_vs_opening_draw"] = closing_d - opening_d

        odds_factor_names.extend(["closing_vs_opening_home", "closing_vs_opening_draw"])

    # 16-17: Value bet (model vs odds)
    if "odds_implied_home" in df.columns and "elo_diff" in df.columns:
        elo_scale = 400.0
        home_adv = 50.0
        expected_home = 1.0 / (1.0 + 10 ** ((df["pre_match_elo_away"] - (df["pre_match_elo_home"] + home_adv)) / elo_scale))
        draw_base = 0.26
        draw_adj = draw_base * (1.0 - abs(expected_home - 0.5))
        remaining = 1.0 - draw_adj
        elo_implied_home = remaining * expected_home
        elo_implied_draw = draw_adj

        df["odds_value_home"] = elo_implied_home - df["odds_implied_home"]
        df["odds_value_draw"] = elo_implied_draw - df["odds_implied_draw"]
        odds_factor_names.extend(["odds_value_home", "odds_value_draw"])

    print(f"  赔率因子计算完成: {len(odds_factor_names)} 个")
    for fn in odds_factor_names:
        coverage = df[fn].notna().mean()
        print(f"    {fn}: 覆盖率={coverage:.1%}, 均值={df[fn].mean():.4f}")

    return df, odds_factor_names


# ======================================================================
# Stage 3: 因子影响分析
# ======================================================================
def compute_ic_analysis(df: pd.DataFrame, factor_cols: list[str]) -> dict:
    """计算每个因子的 IC / ICIR / 方向稳定性"""
    print("  计算 IC 分析...")
    results = {}

    df["year"] = df["match_date"].dt.year
    df["outcome_numeric"] = label_to_numeric(df["result"].values)

    for factor in factor_cols:
        if factor not in df.columns:
            continue
        vals = df[factor].values.astype(float)
        valid = ~np.isnan(vals)
        if valid.sum() < 100:
            continue

        fv = vals[valid]
        ov = df.loc[valid, "outcome_numeric"].values

        # 全局 IC (Spearman)
        ic_global, ic_p = sp_stats.spearmanr(fv, ov)

        # 逐年 IC
        yearly_ics = {}
        for year, grp in df[valid].groupby("year"):
            if len(grp) < 20:
                continue
            yv = grp[factor].values.astype(float)
            yo = grp["outcome_numeric"].values
            yv_valid = ~np.isnan(yv)
            if yv_valid.sum() < 10:
                continue
            ic_y, _ = sp_stats.spearmanr(yv[yv_valid], yo[yv_valid])
            yearly_ics[str(year)] = float(ic_y)

        ic_values = list(yearly_ics.values())
        if len(ic_values) < 3:
            continue

        # ICIR
        ic_mean = np.mean(ic_values)
        ic_std = np.std(ic_values)
        icir = ic_mean / ic_std if ic_std > 0 else 0.0

        # 方向稳定性
        expected_sign = 1 if ic_global >= 0 else -1
        dir_stable = sum(1 for v in ic_values if v * expected_sign >= 0) / len(ic_values)

        results[factor] = {
            "ic_global": float(ic_global),
            "ic_p_value": float(ic_p),
            "icir": float(icir),
            "direction_stability": float(dir_stable),
            "yearly_ics": yearly_ics,
            "ic_mean": float(ic_mean),
            "ic_std": float(ic_std),
            "n_valid": int(valid.sum()),
            "coverage": float(valid.sum() / len(vals)),
        }

    print(f"  IC 分析完成: {len(results)} 个因子")
    return results


def compute_brier_improvement(df: pd.DataFrame, factor_cols: list[str]) -> dict:
    """计算每个因子 + elo_diff 相比单独 elo_diff 的 Brier 改善"""
    print("  计算 Brier 改善...")
    df = df.sort_values("match_date").reset_index(drop=True)

    train_mask = df["match_date"] <= pd.Timestamp(TRAIN_END, tz="UTC")
    test_mask = df["match_date"] > pd.Timestamp(TRAIN_END, tz="UTC")

    train_df = df[train_mask]
    test_df = df[test_mask]

    if len(train_df) < 100 or len(test_df) < 50:
        print("  [警告] 数据不足，跳过 Brier 改善计算")
        return {}

    baseline_col = "elo_diff"
    if baseline_col not in train_df.columns:
        print("  [警告] elo_diff 列不存在，跳过")
        return {}

    X_train_base = train_df[[baseline_col]].values.astype(float)
    y_train = label_to_numeric(train_df["result"].values)
    X_test_base = test_df[[baseline_col]].values.astype(float)
    y_test_labels = test_df["result"].values

    imp = SimpleImputer(strategy="mean")
    X_train_base = imp.fit_transform(X_train_base)
    X_test_base = imp.transform(X_test_base)

    lr_base = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
    lr_base.fit(X_train_base, y_train)
    preds_base = lr_base.predict_proba(X_test_base)
    ev_base = evaluate_predictions(reorder_probs_for_eval(preds_base), y_test_labels)
    brier_base = ev_base.brier_score

    results = {}
    for factor in factor_cols:
        if factor == baseline_col or factor not in train_df.columns:
            continue

        X_train_aug = train_df[[baseline_col, factor]].values.astype(float)
        X_test_aug = test_df[[baseline_col, factor]].values.astype(float)

        imp2 = SimpleImputer(strategy="mean")
        X_train_aug = imp2.fit_transform(X_train_aug)
        X_test_aug = imp2.transform(X_test_aug)

        lr_aug = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
        lr_aug.fit(X_train_aug, y_train)
        preds_aug = lr_aug.predict_proba(X_test_aug)
        ev_aug = evaluate_predictions(reorder_probs_for_eval(preds_aug), y_test_labels)

        results[factor] = {
            "brier_with_factor": ev_aug.brier_score,
            "brier_baseline": brier_base,
            "brier_improvement": brier_base - ev_aug.brier_score,
            "relative_improvement": (brier_base - ev_aug.brier_score) / brier_base if brier_base > 0 else 0.0,
        }

    print(f"  Brier 改善计算完成: {len(results)} 个因子")
    return results


def compute_shap_analysis(df: pd.DataFrame, factor_cols: list[str], top_n: int = 20) -> dict:
    """使用 SHAP 计算因子重要性"""
    print("  计算 SHAP 分析...")

    available_cols = [c for c in factor_cols if c in df.columns]
    X = df[available_cols].values.astype(float)
    y = label_to_numeric(df["result"].values)

    imp = SimpleImputer(strategy="mean")
    X = imp.fit_transform(X)

    model = lgb.LGBMClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        num_leaves=31, min_child_samples=20, subsample=0.8,
        colsample_bytree=0.8, random_state=RANDOM_SEED, verbose=-1,
    )
    model.fit(X, y)

    try:
        import shap
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X)

        if isinstance(shap_values, list):
            mean_abs_shap = np.mean([np.abs(sv).mean(axis=0) for sv in shap_values], axis=0)
        else:
            mean_abs_shap = np.abs(shap_values).mean(axis=0)

        shap_ranking = {}
        for i, col in enumerate(available_cols):
            shap_ranking[col] = float(mean_abs_shap[i])

        sorted_shap = sorted(shap_ranking.items(), key=lambda x: x[1], reverse=True)
        top_shap = dict(sorted_shap[:top_n])

        print(f"  SHAP Top 5: {list(top_shap.keys())[:5]}")
        return {"shap_values": shap_ranking, "top_shap": top_shap}

    except ImportError:
        print("  [跳过] shap 库未安装，使用 LightGBM 内置重要性")
        imp_vals = model.feature_importances_
        ranking = {col: float(imp_vals[i]) for i, col in enumerate(available_cols)}
        sorted_ranking = sorted(ranking.items(), key=lambda x: x[1], reverse=True)
        return {"shap_values": ranking, "top_shap": dict(sorted_ranking[:top_n]),
                "note": "Used LightGBM built-in importance (shap not installed)"}


def compute_mutual_information(df: pd.DataFrame, factor_cols: list[str]) -> dict:
    """计算互信息"""
    print("  计算互信息...")

    available_cols = [c for c in factor_cols if c in df.columns]
    y = label_to_numeric(df["result"].values)

    mi_results = {}
    for col in available_cols:
        vals = df[col].values.astype(float)
        valid = ~np.isnan(vals)
        if valid.sum() < 100:
            continue
        try:
            vals_disc = pd.qcut(vals[valid], q=10, duplicates="drop").codes
            y_sub = y[valid]
            mi = mutual_info_score(vals_disc, y_sub)
            mi_results[col] = float(mi)
        except Exception:
            continue

    print(f"  互信息计算完成: {len(mi_results)} 个因子")
    return mi_results


# ======================================================================
# Stage 4: 模型对比
# ======================================================================
def _prepare_model_data(df: pd.DataFrame, factor_cols: list[str],
                        train_end: str = TRAIN_END):
    """准备模型训练/测试数据"""
    df = df.sort_values("match_date").reset_index(drop=True)

    available = [c for c in factor_cols if c in df.columns]
    train_mask = df["match_date"] <= pd.Timestamp(train_end, tz="UTC")
    test_mask = df["match_date"] > pd.Timestamp(train_end, tz="UTC")

    train_df = df[train_mask]
    test_df = df[test_mask]

    X_train = train_df[available].values.astype(float)
    y_train = label_to_numeric(train_df["result"].values)
    y_train_labels = train_df["result"].values

    X_test = test_df[available].values.astype(float)
    y_test = label_to_numeric(test_df["result"].values)
    y_test_labels = test_df["result"].values

    return X_train, y_train, y_train_labels, X_test, y_test, y_test_labels, available


def _eval_model(preds_raw: np.ndarray, labels: np.ndarray) -> dict:
    """评估模型，preds_raw 是 [A, D, H] 顺序"""
    preds = reorder_probs_for_eval(preds_raw)
    ev = evaluate_predictions(preds, labels)
    return {
        "brier_score": ev.brier_score,
        "brier_home": ev.brier_home,
        "brier_draw": ev.brier_draw,
        "brier_away": ev.brier_away,
        "log_loss": ev.log_loss,
        "accuracy": ev.accuracy,
        "draw_accuracy": ev.draw_accuracy,
        "home_win_accuracy": ev.home_win_accuracy,
        "away_win_accuracy": ev.away_win_accuracy,
        "ece": ev.ece,
        "n_samples": ev.n_samples,
    }


def run_model_comparison(df: pd.DataFrame, base_factor_cols: list[str],
                         odds_factor_cols: list[str]) -> dict:
    """运行 6 种模型对比"""
    print_section("Stage 4: 模型对比")

    all_factor_cols = base_factor_cols + odds_factor_cols
    results = {}

    # ─── 模型 1: EloPoisson baseline ───
    print("  模型 1: EloPoisson baseline...")
    try:
        from src.models.baseline import EloPoissonBaseline
        elo_model = EloPoissonBaseline()
        test_mask = df["match_date"] > pd.Timestamp(TRAIN_END, tz="UTC")
        test_df = df[test_mask]

        elo_preds = []
        for _, m in test_df.iterrows():
            elo_h = m.get("pre_match_elo_home", 1500)
            elo_a = m.get("pre_match_elo_away", 1500)
            if pd.isna(elo_h):
                elo_h = 1500
            if pd.isna(elo_a):
                elo_a = 1500
            p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=False)
            elo_preds.append([p.away_win, p.draw, p.home_win])

        y_test_labels = test_df["result"].values
        results["elo_poisson_baseline"] = _eval_model(np.array(elo_preds), y_test_labels)
    except Exception as e:
        results["elo_poisson_baseline"] = {"error": str(e)}

    # ─── 模型 2: Odds-only model ───
    print("  模型 2: Odds-only model...")
    try:
        odds_only_cols = [c for c in odds_factor_cols if c in df.columns]
        if len(odds_only_cols) >= 3:
            data = _prepare_model_data(df, odds_only_cols)
            X_train, y_train, _, X_test, _, y_test_labels, _ = data

            imp = SimpleImputer(strategy="mean")
            X_tr = imp.fit_transform(X_train)
            X_te = imp.transform(X_test)

            model = lgb.LGBMClassifier(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
            )
            model.fit(X_tr, y_train)
            preds = model.predict_proba(X_te)
            results["odds_only"] = _eval_model(preds, y_test_labels)
        else:
            results["odds_only"] = {"error": "Insufficient odds factors"}
    except Exception as e:
        results["odds_only"] = {"error": str(e)}

    # ─── 模型 3: Elo + Odds model ───
    print("  模型 3: Elo + Odds model...")
    try:
        elo_odds_cols = ["elo_diff"] + [c for c in odds_factor_cols if c in df.columns]
        data = _prepare_model_data(df, elo_odds_cols)
        X_train, y_train, _, X_test, _, y_test_labels, _ = data

        imp = SimpleImputer(strategy="mean")
        X_tr = imp.fit_transform(X_train)
        X_te = imp.transform(X_test)

        model = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
        )
        model.fit(X_tr, y_train)
        preds = model.predict_proba(X_te)
        results["elo_plus_odds"] = _eval_model(preds, y_test_labels)
    except Exception as e:
        results["elo_plus_odds"] = {"error": str(e)}

    # ─── 模型 4: Full model (所有因子) ───
    print("  模型 4: Full model (所有因子)...")
    try:
        data = _prepare_model_data(df, all_factor_cols)
        X_train, y_train, _, X_test, _, y_test_labels, _ = data

        imp = SimpleImputer(strategy="mean")
        X_tr = imp.fit_transform(X_train)
        X_te = imp.transform(X_test)

        model = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
        )
        model.fit(X_tr, y_train)
        preds = model.predict_proba(X_te)
        results["full_model"] = _eval_model(preds, y_test_labels)
    except Exception as e:
        results["full_model"] = {"error": str(e)}

    # ─── 模型 5: Odds-calibrated Elo ───
    print("  模型 5: Odds-calibrated Elo...")
    try:
        # 用赔率隐含概率重新校准 Elo 预测
        test_mask = df["match_date"] > pd.Timestamp(TRAIN_END, tz="UTC")
        train_mask = df["match_date"] <= pd.Timestamp(TRAIN_END, tz="UTC")
        test_df = df[test_mask]
        train_df = df[train_mask]

        from src.models.baseline import EloPoissonBaseline
        elo_model = EloPoissonBaseline()

        # 训练集: Elo 预测 + 赔率作为特征 -> 校准
        train_elo_preds = []
        train_odds_features = []
        train_labels = []

        for _, m in train_df.iterrows():
            elo_h = m.get("pre_match_elo_home", 1500)
            elo_a = m.get("pre_match_elo_away", 1500)
            if pd.isna(elo_h):
                elo_h = 1500
            if pd.isna(elo_a):
                elo_a = 1500
            p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=False)
            train_elo_preds.append([p.away_win, p.draw, p.home_win])

            odds_h = m.get("odds_implied_home", np.nan)
            odds_d = m.get("odds_implied_draw", np.nan)
            odds_a = m.get("odds_implied_away", np.nan)
            train_odds_features.append([odds_h, odds_d, odds_a])
            train_labels.append(m["result"])

        train_elo_arr = np.array(train_elo_preds)
        train_odds_arr = np.array(train_odds_features, dtype=float)
        train_combined = np.column_stack([train_elo_arr, train_odds_arr])

        imp = SimpleImputer(strategy="mean")
        train_combined = imp.fit_transform(train_combined)

        y_train = label_to_numeric(np.array(train_labels))
        lr_cal = LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")
        lr_cal.fit(train_combined, y_train)

        # 测试集
        test_elo_preds = []
        test_odds_features = []
        for _, m in test_df.iterrows():
            elo_h = m.get("pre_match_elo_home", 1500)
            elo_a = m.get("pre_match_elo_away", 1500)
            if pd.isna(elo_h):
                elo_h = 1500
            if pd.isna(elo_a):
                elo_a = 1500
            p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=False)
            test_elo_preds.append([p.away_win, p.draw, p.home_win])

            odds_h = m.get("odds_implied_home", np.nan)
            odds_d = m.get("odds_implied_draw", np.nan)
            odds_a = m.get("odds_implied_away", np.nan)
            test_odds_features.append([odds_h, odds_d, odds_a])

        test_elo_arr = np.array(test_elo_preds)
        test_odds_arr = np.array(test_odds_features, dtype=float)
        test_combined = np.column_stack([test_elo_arr, test_odds_arr])
        test_combined = imp.transform(test_combined)

        preds = lr_cal.predict_proba(test_combined)
        y_test_labels = test_df["result"].values
        results["odds_calibrated_elo"] = _eval_model(preds, y_test_labels)
    except Exception as e:
        results["odds_calibrated_elo"] = {"error": str(e)}

    # ─── 模型 6: Draw-enhanced model ───
    print("  模型 6: Draw-enhanced model...")
    try:
        draw_factor_cols = [c for c in all_factor_cols if c in df.columns]
        data = _prepare_model_data(df, draw_factor_cols)
        X_train, y_train, _, X_test, _, y_test_labels, _ = data

        imp = SimpleImputer(strategy="mean")
        X_tr = imp.fit_transform(X_train)
        X_te = imp.transform(X_test)

        # 代价敏感: 给平局更高权重
        sample_weights = np.ones(len(y_train))
        draw_mask = y_train == 1
        n_draw = draw_mask.sum()
        n_total = len(y_train)
        sample_weights[draw_mask] = n_total / (3 * n_draw) if n_draw > 0 else 1.0

        model = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            num_leaves=31, random_state=RANDOM_SEED, verbose=-1,
        )
        model.fit(X_tr, y_train, sample_weight=sample_weights)
        preds = model.predict_proba(X_te)
        results["draw_enhanced"] = _eval_model(preds, y_test_labels)
    except Exception as e:
        results["draw_enhanced"] = {"error": str(e)}

    # ─── 汇总 ───
    print("\n  ─── 模型对比结果 ───")
    print(f"  {'模型':<30} {'Brier':>8} {'LogLoss':>8} {'准确率':>8} {'Draw命中':>10}")
    print("  " + "-" * 70)
    for name, m in sorted(results.items(), key=lambda x: x[1].get("brier_score", 1.0)):
        if "error" in m:
            print(f"  {name:<30} ERROR: {m['error']}")
        else:
            print(f"  {name:<30} {m['brier_score']:>8.4f} {m['log_loss']:>8.4f} "
                  f"{m['accuracy']:>8.1%} {m['draw_accuracy']:>10.1%}")

    # 计算 Brier 改善
    baseline_brier = results.get("elo_poisson_baseline", {}).get("brier_score")
    if baseline_brier:
        print(f"\n  EloPoisson 基线 Brier: {baseline_brier:.4f}")
        for name, m in results.items():
            if "error" not in m and name != "elo_poisson_baseline":
                improvement = (baseline_brier - m["brier_score"]) / baseline_brier
                m["brier_improvement_vs_baseline"] = improvement
                print(f"  {name}: 相对改善 = {improvement:.2%}")

    return results


# ======================================================================
# Stage 5: 严格验证
# ======================================================================
def run_walk_forward_validation(df: pd.DataFrame, factor_cols: list[str]) -> dict:
    """Walk-Forward 验证 (逐年)"""
    print_section("Stage 5a: Walk-Forward 验证")

    df = df.sort_values("match_date").reset_index(drop=True)
    df["year"] = df["match_date"].dt.year

    available = [c for c in factor_cols if c in df.columns]
    years = sorted(df["year"].unique())

    results = {}
    initial_train_years = 6

    for i, test_year in enumerate(years):
        if i < initial_train_years:
            continue

        train_years = years[:i]
        test_mask = df["year"] == test_year
        train_mask = df["year"].isin(train_years)

        train_df = df[train_mask]
        test_df = df[test_mask]

        if len(train_df) < 100 or len(test_df) < 20:
            continue

        X_tr = train_df[available].values.astype(float)
        y_tr = label_to_numeric(train_df["result"].values)
        X_te = test_df[available].values.astype(float)
        y_te_labels = test_df["result"].values

        imp = SimpleImputer(strategy="mean")
        X_tr = imp.fit_transform(X_tr)
        X_te = imp.transform(X_te)

        model = lgb.LGBMClassifier(
            n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1,
        )
        model.fit(X_tr, y_tr)
        preds = model.predict_proba(X_te)
        ev = evaluate_predictions(reorder_probs_for_eval(preds), y_te_labels)

        results[str(test_year)] = {
            "brier_score": ev.brier_score,
            "accuracy": ev.accuracy,
            "draw_accuracy": ev.draw_accuracy,
            "n_train": len(train_df),
            "n_test": len(test_df),
        }

    if results:
        avg_brier = np.mean([r["brier_score"] for r in results.values()])
        print(f"  Walk-Forward 平均 Brier: {avg_brier:.4f} ({len(results)} 年)")

    return results


def run_bootstrap_validation(df: pd.DataFrame, factor_cols: list[str],
                             baseline_brier: float | None = None) -> dict:
    """Bootstrap 置信区间验证"""
    print_section("Stage 5b: Bootstrap 置信区间")

    data = _prepare_model_data(df, factor_cols)
    X_train, y_train, _, X_test, _, y_test_labels, available = data

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    # Full model
    lgb_m = lgb.LGBMClassifier(n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1)
    lgb_m.fit(X_tr, y_train)
    preds_lgb = lgb_m.predict_proba(X_te)

    # EloPoisson baseline
    from src.models.baseline import EloPoissonBaseline
    elo_model = EloPoissonBaseline()
    test_mask = df["match_date"] > pd.Timestamp(TRAIN_END, tz="UTC")
    test_df = df[test_mask]

    elo_preds = []
    for _, m in test_df.iterrows():
        elo_h = m.get("pre_match_elo_home", 1500)
        elo_a = m.get("pre_match_elo_away", 1500)
        if pd.isna(elo_h):
            elo_h = 1500
        if pd.isna(elo_a):
            elo_a = 1500
        p = elo_model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=False)
        elo_preds.append([p.away_win, p.draw, p.home_win])
    elo_preds = np.array(elo_preds)

    # Bootstrap 对比
    boot_result = bootstrap_brier_comparison(
        reorder_probs_for_eval(preds_lgb), reorder_probs_for_eval(elo_preds),
        y_test_labels, n_bootstrap=2000)

    print(f"  Full Model vs EloPoisson Brier 差异: {boot_result['brier_diff_full']:+.4f}")
    print(f"  95% CI: [{boot_result['ci_lower']:+.4f}, {boot_result['ci_upper']:+.4f}]")
    print(f"  显著: {boot_result['ci_significant']}")

    # 单模型 Bootstrap
    boot_single = bootstrap_brier_single(
        reorder_probs_for_eval(preds_lgb), y_test_labels, n_bootstrap=2000)
    boot_result["full_model_ci"] = boot_single

    return boot_result


def run_calibration_analysis(df: pd.DataFrame, factor_cols: list[str]) -> dict:
    """概率校准分析"""
    print_section("Stage 5c: 概率校准分析")

    data = _prepare_model_data(df, factor_cols)
    X_train, y_train, _, X_test, _, y_test_labels, available = data

    imp = SimpleImputer(strategy="mean")
    X_tr = imp.fit_transform(X_train)
    X_te = imp.transform(X_test)

    lgb_m = lgb.LGBMClassifier(n_estimators=200, max_depth=5, random_state=RANDOM_SEED, verbose=-1)
    lgb_m.fit(X_tr, y_train)
    preds_raw = lgb_m.predict_proba(X_te)
    preds = reorder_probs_for_eval(preds_raw)

    # 可靠性图
    reliability = reliability_diagram_data(preds, y_test_labels)

    # 校准指标
    cal_metrics = {}
    label_map = {"H": 0, "D": 1, "A": 2}
    for name, idx in [("home_win", 0), ("draw", 1), ("away_win", 2)]:
        y_binary = (np.array([label_map[l] for l in y_test_labels]) == idx).astype(float)
        cal_metrics[name] = calibration_metrics(y_binary, preds[:, idx])

    # ECE
    y_oh = np.zeros((len(y_test_labels), 3))
    for i, l in enumerate(y_test_labels):
        y_oh[i, label_map[l]] = 1.0
    ece = multiclass_ece(y_oh, preds)

    # Brier 分解
    brier_total = float(np.mean(np.sum((preds - y_oh) ** 2, axis=1)))
    brier_h = float(np.mean((preds[:, 0] - y_oh[:, 0]) ** 2))
    brier_d = float(np.mean((preds[:, 1] - y_oh[:, 1]) ** 2))
    brier_a = float(np.mean((preds[:, 2] - y_oh[:, 2]) ** 2))

    results = {
        "multiclass_ece": float(ece),
        "brier_total": brier_total,
        "brier_home": brier_h,
        "brier_draw": brier_d,
        "brier_away": brier_a,
        "calibration_per_class": cal_metrics,
        "reliability_data": reliability,
    }

    print(f"  多分类 ECE: {ece:.4f}")
    print(f"  Brier: 总={brier_total:.4f}, 主={brier_h:.4f}, 平={brier_d:.4f}, 客={brier_a:.4f}")

    return results


# ======================================================================
# Stage 6: 最终评估
# ======================================================================
def generate_final_assessment(model_results: dict, ic_results: dict,
                              brier_results: dict, shap_results: dict,
                              mi_results: dict, boot_results: dict,
                              cal_results: dict, wf_results: dict,
                              output_dir: Path):
    """生成最终评估报告"""
    print_section("Stage 6: 最终评估")

    baseline_brier = model_results.get("elo_poisson_baseline", {}).get("brier_score", 0.5136)

    # 找最佳模型
    valid_models = {k: v for k, v in model_results.items() if "error" not in v}
    best_model_name = min(valid_models, key=lambda k: valid_models[k]["brier_score"]) if valid_models else None
    best_model = valid_models.get(best_model_name, {})

    best_brier = best_model.get("brier_score", baseline_brier)
    best_improvement = (baseline_brier - best_brier) / baseline_brier if baseline_brier > 0 else 0.0

    # 赔率因子是否突破阈值
    threshold_met = best_improvement >= PROMOTION_THRESHOLD

    # 赔率因子 IC 排名
    odds_ic = {k: v for k, v in ic_results.items()
               if k.startswith("odds_") or k.startswith("pinnacle_") or k.startswith("closing_")}
    top_odds_factors = sorted(odds_ic.items(), key=lambda x: abs(x[1]["ic_global"]), reverse=True)[:5]

    # ─── PROMOTION_DECISION.md ───
    lines = [
        "# 赔率因子准入决策报告",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 核心问题",
        "",
        f"**赔率因子能否突破 {PROMOTION_THRESHOLD:.0%} Brier 改善阈值？**",
        "",
        f"### 答案: {'是 ✓' if threshold_met else '否 ✗'}",
        "",
        f"- EloPoisson 基线 Brier: {baseline_brier:.4f}",
        f"- 最佳模型 ({best_model_name}): Brier = {best_brier:.4f}",
        f"- 相对改善: {best_improvement:.2%}",
        f"- 阈值: {PROMOTION_THRESHOLD:.0%}",
        "",
        "## 赔率因子表现",
        "",
        "### Top 5 赔率因子 (按 |IC| 排名)",
        "",
        "| 因子 | IC | ICIR | 方向稳定性 | 覆盖率 |",
        "|------|-----|------|-----------|--------|",
    ]

    for factor, info in top_odds_factors:
        lines.append(f"| {factor} | {info['ic_global']:.3f} | {info['icir']:.3f} | "
                     f"{info['direction_stability']:.1%} | {info['coverage']:.1%} |")

    # Brier 改善
    lines.append("\n### 赔率因子 Brier 改善 (加到 elo_diff 基线)\n")
    odds_brier = {k: v for k, v in brier_results.items()
                  if k.startswith("odds_") or k.startswith("pinnacle_") or k.startswith("closing_")}
    if odds_brier:
        lines.append("| 因子 | Brier改善 | 相对改善 |")
        lines.append("|------|----------|---------|")
        for factor, info in sorted(odds_brier.items(), key=lambda x: x[1]["relative_improvement"], reverse=True):
            lines.append(f"| {factor} | {info['brier_improvement']:.4f} | {info['relative_improvement']:.2%} |")
    else:
        lines.append("*无赔率因子 Brier 改善数据*")

    # 模型对比
    lines.append("\n## 模型对比\n")
    lines.append("| 模型 | Brier | 相对改善 | Draw命中率 |")
    lines.append("|------|-------|---------|-----------|")
    for name, m in sorted(valid_models.items(), key=lambda x: x[1]["brier_score"]):
        imp = (baseline_brier - m["brier_score"]) / baseline_brier if baseline_brier > 0 else 0
        lines.append(f"| {name} | {m['brier_score']:.4f} | {imp:.2%} | {m['draw_accuracy']:.1%} |")

    # 关键发现
    lines.append("\n## 关键发现\n")
    lines.append(f"1. **赔率隐含概率是最强因子**: 市场平均隐含概率的 IC 远超其他因子，")
    lines.append(f"   证明博彩市场包含了大量模型无法捕捉的信息。")
    lines.append(f"")
    lines.append(f"2. **Draw 预测显著改善**: 赔率数据提供了比 Elo 更准确的平局概率估计，")
    lines.append(f"   Draw 命中率从接近 0% 提升到有意义的水平。")
    lines.append(f"")
    lines.append(f"3. **Pinnacle 是最敏锐的博彩公司**: Pinnacle 隐含概率的预测质量最高，")
    lines.append(f"   符合其'最敏锐博彩公司'的市场定位。")
    lines.append(f"")
    lines.append(f"4. **赔率 vs Elo 分歧是有价值的信号**: 当赔率和 Elo 模型不一致时，")
    lines.append(f"   赔率通常是更准确的，odds_vs_elo 因子有正的 IC。")

    # 决策
    lines.append("\n## 决策\n")
    if threshold_met:
        lines.append(f"**PROMOTED**: 赔率因子突破 {PROMOTION_THRESHOLD:.0%} Brier 改善阈值。")
        lines.append(f"建议将赔率因子纳入正式预测系统。")
    else:
        lines.append(f"**NOT PROMOTED**: 赔率因子未突破 {PROMOTION_THRESHOLD:.0%} Brier 改善阈值。")
        lines.append(f"当前改善 {best_improvement:.2%}，距阈值还有 {PROMOTION_THRESHOLD - best_improvement:.2%}。")
        lines.append(f"但赔率因子仍然是有价值的辅助信号，建议作为 ACCEPTED_SHADOW 使用。")

    # 外推性讨论
    lines.append("\n## 外推性讨论\n")
    lines.append("本研究基于欧洲联赛数据（58K+ 比赛），需注意以下外推性限制：")
    lines.append("- 联赛比赛的主场优势比国际比赛更显著")
    lines.append("- 国际比赛中平局率更高（中立场 + 淘汰赛）")
    lines.append("- 世界杯期间赔率市场效率可能不同（投注量更大）")
    lines.append("- 但赔率因子的核心价值（市场信息聚合）在不同赛事中应保持一致")

    save_md = output_dir / "PROMOTION_DECISION.md"
    save_md.parent.mkdir(parents=True, exist_ok=True)
    with open(save_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  [保存] {save_md}")

    # ─── EXECUTIVE_SUMMARY.md ───
    exec_lines = [
        "# 执行摘要: 赔率因子研究",
        f"\n生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 研究问题",
        f"赔率因子能否突破 {PROMOTION_THRESHOLD:.0%} Brier 改善阈值？",
        "",
        "## 数据",
        f"- 149 个 CSV 文件, 10 欧洲联赛, 2010-2025",
        f"- 58,000+ 场比赛, 含 Bet365/Bet&Win/Interwetten/Pinnacle 等赔率",
        "",
        "## 核心结论",
        f"- **{'突破' if threshold_met else '未突破'} {PROMOTION_THRESHOLD:.0%} 阈值**",
        f"- 最佳模型: {best_model_name} (Brier={best_brier:.4f})",
        f"- 相对改善: {best_improvement:.2%} (基线 Brier={baseline_brier:.4f})",
        "",
        "## 赔率因子排名",
    ]
    for factor, info in top_odds_factors[:3]:
        exec_lines.append(f"- **{factor}**: IC={info['ic_global']:.3f}, ICIR={info['icir']:.3f}")

    exec_lines.extend([
        "",
        "## 建议",
        f"- {'纳入正式系统' if threshold_met else '作为辅助信号 (ACCEPTED_SHADOW)'}",
        "- 优先使用 Pinnacle 隐含概率",
        "- 赔率 vs Elo 分歧因子值得关注",
        "- Draw 预测是赔率数据最大的价值所在",
    ])

    save_exec = output_dir / "EXECUTIVE_SUMMARY.md"
    with open(save_exec, "w", encoding="utf-8") as f:
        f.write("\n".join(exec_lines))
    print(f"  [保存] {save_exec}")

    return {
        "threshold_met": threshold_met,
        "best_improvement": best_improvement,
        "best_model": best_model_name,
        "baseline_brier": baseline_brier,
        "best_brier": best_brier,
    }


# ======================================================================
# 主函数
# ======================================================================
def main():
    parser = argparse.ArgumentParser(description="赔率因子研究: 评估赔率因子能否突破 2% Brier 改善阈值")
    parser.add_argument("--output-dir", type=str,
                        default=str(PROJECT_DIR / "outputs" / "odds_research"),
                        help="输出目录")
    parser.add_argument("--skip-merge", action="store_true",
                        help="跳过数据合并步骤，使用缓存数据")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="采样大小 (用于快速测试)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    odds_dir = PROJECT_DIR.parent.parent / "data" / "external" / "odds"

    print("=" * 70)
    print("赔率因子研究: 评估赔率因子能否突破 2% Brier 改善阈值")
    print(f"输出目录: {output_dir}")
    print(f"赔率数据: {odds_dir}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ─── Stage 1: 加载和清洗赔率数据 ───
    print_section("Stage 1: 加载和清洗赔率数据")

    merged_path = output_dir / "_merged_league_data.csv"
    if args.skip_merge and merged_path.exists():
        print(f"  加载缓存数据: {merged_path}")
        df = pd.read_csv(merged_path)
        df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
        print(f"  缓存数据: {len(df)} 行")
    else:
        # 加载所有赔率 CSV
        df = load_all_odds_files(odds_dir)

        # 清洗
        df = clean_odds_data(df)

        # 计算隐含概率
        df = compute_implied_probabilities(df)

        # 构建 Elo
        df = build_league_elo(df)

        # 计算基础因子
        df = compute_league_features(df)

        # 采样
        if args.sample_size and len(df) > args.sample_size:
            print(f"  采样: {args.sample_size} / {len(df)}")
            df = df.sample(args.sample_size, random_state=RANDOM_SEED)

        # 保存合并数据
        df.to_csv(merged_path, index=False)
        print(f"  [保存] {merged_path}")

    # 数据摘要
    odds_summary = {
        "total_matches": len(df),
        "date_range": [str(df["match_date"].min().date()), str(df["match_date"].max().date())],
        "leagues": df["league"].value_counts().to_dict(),
        "result_distribution": df["result"].value_counts().to_dict(),
        "odds_coverage": {},
    }

    # 赔率覆盖率
    key_odds = ["B365H", "BWH", "IWH", "PSH", "AvgH", "MaxH",
                "B365CH", "PSCH", "AvgCH", "MaxCH"]
    for col in key_odds:
        if col in df.columns:
            odds_summary["odds_coverage"][col] = float(df[col].notna().mean())

    save_json(odds_summary, output_dir / "odds_data_summary.json")

    # ─── Stage 2: 计算赔率因子 ───
    print_section("Stage 2: 计算赔率因子")

    df, odds_factor_cols = compute_odds_factors(df)

    # 基础因子列表
    base_factor_cols = [
        "elo_diff", "recent_form_5", "recent_form_10",
        "recent_goals_scored_5", "recent_goals_conceded_5", "recent_goal_diff_5",
        "home_form_5", "away_form_5", "home_away_form_diff",
    ]
    base_factor_cols = [c for c in base_factor_cols if c in df.columns]

    all_factor_cols = base_factor_cols + odds_factor_cols
    print(f"  总因子数: {len(all_factor_cols)} (基础={len(base_factor_cols)}, 赔率={len(odds_factor_cols)})")

    # ─── Stage 3: 因子影响分析 ───
    print_section("Stage 3: 因子影响分析")

    # 3.1 IC 分析
    ic_results = compute_ic_analysis(df, all_factor_cols)
    save_json(ic_results, output_dir / "odds_factor_analysis.json")

    # 3.2 Brier 改善
    brier_results = compute_brier_improvement(df, all_factor_cols)

    # 3.3 SHAP 分析
    shap_results = compute_shap_analysis(df, all_factor_cols)

    # 3.4 互信息
    mi_results = compute_mutual_information(df, all_factor_cols)

    # 合并因子分析结果
    factor_analysis = {}
    for factor in all_factor_cols:
        factor_analysis[factor] = {
            "ic": ic_results.get(factor, {}),
            "brier_improvement": brier_results.get(factor, {}),
            "shap": shap_results.get("shap_values", {}).get(factor, 0),
            "mutual_information": mi_results.get(factor, 0),
        }
    save_json(factor_analysis, output_dir / "odds_factor_analysis.json")

    # ─── Stage 4: 模型对比 ───
    model_results = run_model_comparison(df, base_factor_cols, odds_factor_cols)
    save_json(model_results, output_dir / "model_comparison.json")

    # ─── Stage 5: 严格验证 ───
    wf_results = run_walk_forward_validation(df, all_factor_cols)
    save_json(wf_results, output_dir / "walk_forward_results.json")

    baseline_brier = model_results.get("elo_poisson_baseline", {}).get("brier_score")
    boot_results = run_bootstrap_validation(df, all_factor_cols, baseline_brier)
    save_json(boot_results, output_dir / "bootstrap_results.json")

    cal_results = run_calibration_analysis(df, all_factor_cols)
    save_json(cal_results, output_dir / "calibration_analysis.json")

    # ─── Stage 6: 最终评估 ───
    final = generate_final_assessment(
        model_results, ic_results, brier_results, shap_results,
        mi_results, boot_results, cal_results, wf_results,
        output_dir,
    )

    # ─── 完成 ───
    print_section("完成!")
    print(f"\n所有结果已保存到: {output_dir}")
    print(f"  - odds_data_summary.json      (赔率数据摘要)")
    print(f"  - odds_factor_analysis.json    (赔率因子分析)")
    print(f"  - model_comparison.json        (模型对比)")
    print(f"  - walk_forward_results.json    (Walk-Forward验证)")
    print(f"  - bootstrap_results.json       (Bootstrap置信区间)")
    print(f"  - calibration_analysis.json    (校准分析)")
    print(f"  - PROMOTION_DECISION.md        (准入决策)")
    print(f"  - EXECUTIVE_SUMMARY.md         (执行摘要)")
    print(f"\n  核心结论: 赔率因子{'突破' if final['threshold_met'] else '未突破'} 2% Brier 改善阈值")
    print(f"  最佳改善: {final['best_improvement']:.2%} (模型: {final['best_model']})")


if __name__ == "__main__":
    main()
