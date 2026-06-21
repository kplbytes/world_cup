#!/usr/bin/env python3
"""多源数据增强研究流水线

加载已有特征缓存，尝试用 API 数据增强特征，运行完整验证流水线，
并与 deep_mining 结果对比。

即使没有 API key 也能运行（使用缓存数据或跳过不可用的数据源）。

用法:
    python -m scripts.run_augmented_research [--output-dir DIR] [--skip-api] [--skip-features]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# ── 项目路径 ──
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import (
    load_international_results,
    filter_by_date,
    validate_data,
    load_fifa_rankings,
    inject_fifa_rankings,
    _FIFA_NAME_MAP,
    _CONFEDERATION_MAP,
    TOURNAMENT_CATEGORIES,
    TOURNAMENT_WEIGHTS,
)
from src.data.api_clients import (
    FootballDataOrgClient,
    APIFootballClient,
    OpenMeteoClient,
    StatsBombLoader,
    OpenFootballLoader,
)
from src.data.feature_augmentor import (
    augment_all,
    AUGMENTED_FACTOR_COLUMNS,
    AUGMENTED_FACTOR_GROUPS,
)
from src.features.as_of import MatchView, compute_all_features, compute_features_at_time
from src.features.calculator import FACTOR_FUNCTIONS
from src.models.baseline import (
    HomeFixedBaseline,
    FrequencyBaseline,
    EloLogisticBaseline,
    EloPoissonBaseline,
    LightGBMBaseline,
    RegularizedLogisticBaseline,
    Prediction,
)
from src.evaluation.metrics import (
    evaluate_predictions,
    compare_models,
    brier_score,
    brier_skill_score,
    compute_factor_direction_stability,
    EvaluationResult,
)
from src.evaluation.backtest import (
    time_based_split,
    walk_forward_validation,
)
from src.evaluation.bootstrap import bootstrap_brier_comparison
from src.evaluation.calibration import (
    isotonic_calibration,
    reliability_diagram_data,
)
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.feature_selection import mutual_info_classif
from sklearn.inspection import permutation_importance

warnings.filterwarnings("ignore")

# ── 常量 ──
RANDOM_SEED = 42
DATA_START = "2000-01-01"
TRAIN_END = "2018-12-31"
VAL_START = "2019-01-01"
VAL_END = "2025-12-31"
WORLD_CUP_YEARS = [2010, 2014, 2018, 2022]

DATA_DIR = PROJECT_DIR.parent.parent / "data" / "external"
DEEP_MINING_DIR = PROJECT_DIR / "outputs" / "deep_mining"


# ============================================================
# 数据加载
# ============================================================

def load_all_data(sample_size: int | None = None) -> dict:
    """加载并清洗所有数据源。"""
    print_section("Phase 1: 数据加载与清洗")

    # 1. 主比赛结果
    results_path = DATA_DIR / "results.csv"
    print(f"  加载比赛结果: {results_path}")
    results = pd.read_csv(results_path)
    results = results.rename(columns={
        "date": "match_date", "home_score": "home_goals", "away_score": "away_goals",
    })
    results["match_date"] = pd.to_datetime(results["match_date"], utc=True)
    results["result"] = results.apply(
        lambda r: "H" if r["home_goals"] > r["away_goals"]
        else ("A" if r["home_goals"] < r["away_goals"] else "D"),
        axis=1,
    )
    results["is_neutral"] = results["neutral"].astype(bool)
    results["source"] = "results_csv"
    results["fetched_at"] = "2025-12-31"
    results["available_before_kickoff"] = True
    results["match_id"] = results.apply(
        lambda r: hashlib.md5(f"{r['match_date']}_{r['home_team']}_{r['away_team']}".encode()).hexdigest()[:12],
        axis=1,
    )
    results["tournament_category"] = results["tournament"].apply(_categorize_tournament)
    results["match_weight"] = results["tournament_category"].map(TOURNAMENT_WEIGHTS).fillna(0.7)
    results["is_official"] = results["tournament_category"] != "friendly"
    results["home_confederation"] = results["home_team"].map(_CONFEDERATION_MAP.get, na_action="ignore").fillna("Unknown")
    results["away_confederation"] = results["away_team"].map(_CONFEDERATION_MAP.get, na_action="ignore").fillna("Unknown")
    results["is_cross_confederation"] = results["home_confederation"] != results["away_confederation"]
    results["kickoff_utc"] = results["match_date"]
    results["effective_at"] = results["match_date"].dt.strftime("%Y-%m-%d")
    print(f"    比赛记录: {len(results)}")

    # 2. 点球大战
    shootouts_path = DATA_DIR / "shootouts.csv"
    print(f"  加载点球大战: {shootouts_path}")
    shootouts = pd.read_csv(shootouts_path)
    shootouts = shootouts.rename(columns={"date": "match_date"})
    shootouts["match_date"] = pd.to_datetime(shootouts["match_date"], utc=True)
    shootout_keys = set()
    for _, row in shootouts.iterrows():
        key = f"{row['match_date'].strftime('%Y-%m-%d')}_{row['home_team']}_{row['away_team']}"
        shootout_keys.add(key)
    results["is_shootout"] = results.apply(
        lambda r: f"{r['match_date'].strftime('%Y-%m-%d')}_{r['home_team']}_{r['away_team']}" in shootout_keys,
        axis=1,
    )

    # 3. FIFA 排名
    fifa_path = DATA_DIR / "fifa_ranking.csv"
    print(f"  加载 FIFA 排名: {fifa_path}")
    fifa_rankings = _load_fifa_rankings_custom(fifa_path)
    print(f"    排名记录: {len(fifa_rankings)}")

    # 日期过滤
    results = filter_by_date(results, DATA_START, VAL_END)
    print(f"  过滤后比赛: {len(results)}")

    if sample_size is not None and len(results) > sample_size:
        results = results.sample(sample_size, random_state=RANDOM_SEED)
        print(f"  采样后比赛: {len(results)}")

    return {
        "matches": results,
        "shootouts": shootouts,
        "fifa_rankings": fifa_rankings,
    }


def _categorize_tournament(tournament: str) -> str:
    """将赛事名称映射到赛事类别。"""
    tournament_lower = tournament.lower()
    if tournament in TOURNAMENT_CATEGORIES:
        return TOURNAMENT_CATEGORIES[tournament]
    if "world cup" in tournament_lower and "qualif" not in tournament_lower:
        return "world_cup"
    if "world cup" in tournament_lower and "qualif" in tournament_lower:
        return "qualification"
    if "friendly" in tournament_lower:
        return "friendly"
    if "euro" in tournament_lower and "qualif" not in tournament_lower:
        return "continental"
    if "euro" in tournament_lower and "qualif" in tournament_lower:
        return "qualification"
    if "copa" in tournament_lower:
        return "continental"
    if "african" in tournament_lower or "afcon" in tournament_lower:
        return "qualification" if "qualif" in tournament_lower else "continental"
    if "asian cup" in tournament_lower:
        return "qualification" if "qualif" in tournament_lower else "continental"
    if "gold cup" in tournament_lower:
        return "qualification" if "qualif" in tournament_lower else "continental"
    if "nations league" in tournament_lower:
        return "nations_league"
    if "qualif" in tournament_lower:
        return "qualification"
    return "other"


def _load_fifa_rankings_custom(csv_path: Path) -> pd.DataFrame:
    """加载 FIFA 排名数据。"""
    df = pd.read_csv(csv_path)
    col_map = {}
    for c in df.columns:
        cl = c.lower().strip()
        if cl in ("rank",):
            col_map[c] = "fifa_rank"
        elif cl in ("country_full", "team", "team_name", "country"):
            col_map[c] = "team_name"
        elif cl in ("total_points", "points", "fifa_points"):
            col_map[c] = "fifa_points"
        elif cl in ("rank_date", "date"):
            col_map[c] = "rank_date"
        elif cl in ("previous_points",):
            col_map[c] = "previous_points"
    df = df.rename(columns=col_map)
    if "fifa_rank" not in df.columns and "fifa_points" in df.columns and "rank_date" in df.columns:
        df["fifa_rank"] = df.groupby("rank_date")["fifa_points"].rank(ascending=False, method="min").astype("Int64")
    keep_cols = [c for c in ["team_name", "fifa_rank", "fifa_points", "rank_date", "previous_points"] if c in df.columns]
    df = df[keep_cols]
    if "rank_date" in df.columns:
        df["rank_date"] = pd.to_datetime(df["rank_date"], utc=True)
    else:
        return pd.DataFrame()
    df["team_name"] = df["team_name"].apply(lambda n: _FIFA_NAME_MAP.get(n, n))
    df = df.sort_values("rank_date").reset_index(drop=True)
    return df


# ============================================================
# API 数据采集
# ============================================================

def collect_api_data(
    matches: pd.DataFrame,
    skip_api: bool = False,
) -> dict:
    """尝试从各 API 采集增强数据。

    即使某些 API 不可用（无 key），也会优雅降级。

    Args:
        matches: 比赛 DataFrame
        skip_api: 是否跳过 API 调用

    Returns:
        数据源可用性报告和采集到的数据
    """
    print_section("Phase 2: 多源数据采集")

    sources_status = {}
    collected = {
        "injuries_data": None,
        "odds_data": None,
        "weather_data": None,
        "xg_data": None,
        "coach_data": None,
        "lineup_data": None,
    }

    if skip_api:
        print("  [SKIP] 跳过所有 API 调用（--skip-api）")
        for name in ["football_data_org", "api_football", "open_meteo", "statsbomb", "openfootball"]:
            sources_status[name] = "skipped"
        return {"status": sources_status, "data": collected}

    # 1. football-data.org
    print("  检查 football-data.org...")
    fd_client = FootballDataOrgClient()
    if fd_client.is_available:
        sources_status["football_data_org"] = "available"
        print("    [OK] football-data.org API token 已配置")
        # 尝试获取世界杯比赛数据（用于后续匹配）
        try:
            for year in WORLD_CUP_YEARS:
                wc_matches = fd_client.get_world_cup_matches(year)
                print(f"    {year} 世界杯: {len(wc_matches)} 场比赛")
        except Exception as e:
            print(f"    [WARNING] football-data.org 获取失败: {e}")
    else:
        sources_status["football_data_org"] = "no_token"
        print("    [SKIP] 无 FOOTBALL_DATA_ORG_TOKEN 环境变量")

    # 2. API-Football
    print("  检查 API-Football...")
    af_client = APIFootballClient()
    if af_client.is_available:
        sources_status["api_football"] = "available"
        print("    [OK] API-Football API key 已配置")

        # 尝试获取赔率数据
        try:
            odds_list = []
            # 获取世界杯比赛的赔率（受限于 100 req/day）
            wc_matches = matches[matches["tournament_category"] == "world_cup"]
            sample_wc = wc_matches.head(5)  # 限制请求数
            for _, m in sample_wc.iterrows():
                # 注意：这里需要 fixture_id 映射，简化处理
                pass
            if odds_list:
                collected["odds_data"] = odds_list
        except Exception as e:
            print(f"    [WARNING] API-Football 赔率获取失败: {e}")

        # 尝试获取教练数据
        try:
            coach_info = {}
            wc_teams = set(wc_matches["home_team"].unique()) | set(wc_matches["away_team"].unique())
            for team in list(wc_teams)[:5]:  # 限制请求数
                coach = af_client.get_coach(team_id=0)  # 需要 team_id 映射
                if coach:
                    coach_info[team] = coach
            if coach_info:
                collected["coach_data"] = coach_info
        except Exception as e:
            print(f"    [WARNING] API-Football 教练获取失败: {e}")
    else:
        sources_status["api_football"] = "no_key"
        print("    [SKIP] 无 API_FOOTBALL_KEY 环境变量")

    # 3. Open-Meteo（无需 API key）
    print("  检查 Open-Meteo...")
    try:
        meteo_client = OpenMeteoClient()
        sources_status["open_meteo"] = "available"
        print("    [OK] Open-Meteo 无需认证")

        # 获取世界杯比赛天气
        weather_list = []
        wc_matches = matches[matches["tournament_category"] == "world_cup"]
        # 限制请求数，采样获取
        sample_wc = wc_matches.head(20)
        for _, m in sample_wc.iterrows():
            city = str(m.get("city", m.get("country", "")))
            date = str(m["match_date"].date()) if hasattr(m["match_date"], "date") else str(m["match_date"])[:10]
            if city and city != "nan":
                w = meteo_client.get_weather_for_match(city, date)
                if w:
                    w["date"] = date
                    w["city"] = city
                    weather_list.append(w)
        if weather_list:
            collected["weather_data"] = weather_list
            print(f"    获取到 {len(weather_list)} 条天气数据")
    except Exception as e:
        sources_status["open_meteo"] = f"error: {e}"
        print(f"    [WARNING] Open-Meteo 获取失败: {e}")

    # 4. StatsBomb（无需 API key）
    print("  检查 StatsBomb Open Data...")
    try:
        sb_loader = StatsBombLoader()
        sources_status["statsbomb"] = "available"
        print("    [OK] StatsBomb 开放数据可用")

        # 尝试获取世界杯 xG 数据
        xg_info = {}
        for year in [2018, 2022]:  # 仅处理有数据的年份
            sb_matches = sb_loader.get_world_cup_matches(year)
            if sb_matches:
                print(f"    {year} 世界杯: {len(sb_matches)} 场 StatsBomb 比赛")
                # 提取 xG 数据
                for match in sb_matches[:5]:  # 限制处理量
                    match_id = match.get("match_id")
                    if match_id:
                        events = sb_loader.get_events(match_id)
                        if events:
                            xg_extracted = sb_loader.extract_xg_from_events(events)
                            # 简化存储
                            home_team = match.get("home_team", {}).get("home_team_name", "")
                            away_team = match.get("away_team", {}).get("away_team_name", "")
                            if home_team and xg_extracted.get("home_xg"):
                                if home_team not in xg_info:
                                    xg_info[home_team] = {"matches": []}
                                xg_info[home_team]["matches"].append({
                                    "date": match.get("match_date", ""),
                                    "xg_for": sum(xg_extracted["home_xg"]),
                                    "xg_against": sum(xg_extracted.get("away_xg", [0])),
                                    "goals_for": match.get("home_score", 0),
                                    "goals_against": match.get("away_score", 0),
                                })
        if xg_info:
            collected["xg_data"] = xg_info
            print(f"    提取到 {len(xg_info)} 支球队的 xG 数据")
    except Exception as e:
        sources_status["statsbomb"] = f"error: {e}"
        print(f"    [WARNING] StatsBomb 获取失败: {e}")

    # 5. openfootball
    print("  检查 openfootball...")
    try:
        of_loader = OpenFootballLoader()
        sources_status["openfootball"] = "available"
        print("    [OK] openfootball 数据可用")
    except Exception as e:
        sources_status["openfootball"] = f"error: {e}"
        print(f"    [WARNING] openfootball 获取失败: {e}")

    # 打印汇总
    print("\n  数据源可用性汇总:")
    for name, status in sources_status.items():
        icon = "✓" if status == "available" else "✗" if "no_" in status else "?" if status == "skipped" else "!"
        print(f"    {icon} {name}: {status}")

    return {"status": sources_status, "data": collected}


# ============================================================
# FIFA 排名注入
# ============================================================

def inject_fifa_data(matches: pd.DataFrame, fifa_rankings: pd.DataFrame) -> pd.DataFrame:
    """将 FIFA 排名数据注入比赛数据（as-of 逻辑）。"""
    if fifa_rankings.empty:
        matches["fifa_rank_diff_injected"] = np.nan
        matches["fifa_points_diff_injected"] = np.nan
        matches["fifa_rank_trend_home_injected"] = np.nan
        matches["fifa_rank_trend_away_injected"] = np.nan
        matches["elo_fifa_disagreement_injected"] = np.nan
        return matches

    print("  注入 FIFA 排名数据...")
    matches = matches.copy()

    rank_dates = fifa_rankings["rank_date"]
    if rank_dates.dt.tz is not None:
        rank_dates = rank_dates.dt.tz_localize(None)
    rank_date_values = rank_dates.sort_values().unique()

    home_ranks, away_ranks = [], []
    home_points, away_points = [], []
    home_trends, away_trends = [], []
    disagreements = []

    for _, row in matches.iterrows():
        match_dt = row["match_date"]
        if hasattr(match_dt, "tz") and match_dt.tz is not None:
            match_dt = match_dt.tz_localize(None)

        prior_dates = rank_date_values[rank_date_values <= match_dt]
        if len(prior_dates) == 0:
            home_ranks.append(np.nan)
            away_ranks.append(np.nan)
            home_points.append(np.nan)
            away_points.append(np.nan)
            home_trends.append(np.nan)
            away_trends.append(np.nan)
            disagreements.append(np.nan)
            continue

        latest_rank_date = prior_dates[-1]
        rank_snapshot = fifa_rankings[rank_dates.values == latest_rank_date]

        home_rank_row = rank_snapshot[rank_snapshot["team_name"] == row["home_team"]]
        away_rank_row = rank_snapshot[rank_snapshot["team_name"] == row["away_team"]]

        h_rank = float(home_rank_row["fifa_rank"].values[0]) if len(home_rank_row) > 0 else np.nan
        a_rank = float(away_rank_row["fifa_rank"].values[0]) if len(away_rank_row) > 0 else np.nan
        h_points = float(home_rank_row["fifa_points"].values[0]) if len(home_rank_row) > 0 and "fifa_points" in home_rank_row.columns else np.nan
        a_points = float(away_rank_row["fifa_points"].values[0]) if len(away_rank_row) > 0 and "fifa_points" in away_rank_row.columns else np.nan

        home_ranks.append(h_rank)
        away_ranks.append(a_rank)
        home_points.append(h_points)
        away_points.append(a_points)

        six_months_ago = match_dt - pd.Timedelta(days=180)
        prior_6m = rank_date_values[rank_date_values <= six_months_ago]
        if len(prior_6m) > 0:
            old_date = prior_6m[-1]
            old_snapshot = fifa_rankings[rank_dates.values == old_date]
            old_h = old_snapshot[old_snapshot["team_name"] == row["home_team"]]
            old_a = old_snapshot[old_snapshot["team_name"] == row["away_team"]]
            h_trend = float(old_h["fifa_rank"].values[0] - h_rank) if len(old_h) > 0 and not np.isnan(h_rank) else np.nan
            a_trend = float(old_a["fifa_rank"].values[0] - a_rank) if len(old_a) > 0 and not np.isnan(a_rank) else np.nan
        else:
            h_trend = np.nan
            a_trend = np.nan
        home_trends.append(h_trend)
        away_trends.append(a_trend)

        elo_d = row.get("elo_diff", None)
        if elo_d is not None and not np.isnan(elo_d) and not np.isnan(h_rank) and not np.isnan(a_rank):
            elo_signal = np.sign(float(elo_d))
            fifa_signal = np.sign(float(a_rank) - float(h_rank))
            disagreements.append(float(abs(elo_signal - fifa_signal)))
        else:
            disagreements.append(np.nan)

    matches["fifa_rank_diff_injected"] = pd.to_numeric(home_ranks, errors="coerce") - pd.to_numeric(away_ranks, errors="coerce")
    matches["fifa_points_diff_injected"] = pd.to_numeric(home_points, errors="coerce") - pd.to_numeric(away_points, errors="coerce")
    matches["fifa_rank_trend_home_injected"] = home_trends
    matches["fifa_rank_trend_away_injected"] = away_trends
    matches["elo_fifa_disagreement_injected"] = disagreements

    coverage = matches["fifa_rank_diff_injected"].notna().mean()
    print(f"    FIFA 排名覆盖率: {coverage:.1%}")

    return matches


# ============================================================
# 深度挖掘因子（复用 run_deep_mining.py 的因子定义）
# ============================================================

def _form_score(outcomes) -> float:
    score_map = {"W": 1.0, "D": 0.5, "L": 0.0}
    vals = [score_map.get(o, 0.0) for o in outcomes] if not isinstance(outcomes, np.ndarray) else outcomes
    return float(np.mean(vals)) if len(vals) > 0 else 0.0


def draw_tendency_home(home_view, away_view, match):
    matches = home_view.recent_matches(20)
    if len(matches) < 3:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    return float((outcomes == "D").mean())


def draw_tendency_away(home_view, away_view, match):
    matches = away_view.recent_matches(20)
    if len(matches) < 3:
        return None
    outcomes = away_view.get_team_outcomes(matches)
    return float((outcomes == "D").mean())


def draw_tendency_diff(home_view, away_view, match):
    h = draw_tendency_home(home_view, away_view, match)
    a = draw_tendency_away(home_view, away_view, match)
    if h is None or a is None:
        return None
    return h - a


def elo_closeness(home_view, away_view, match):
    elo_d = match.get("elo_diff", None)
    if elo_d is None or pd.isna(elo_d):
        return None
    return 1.0 - min(abs(float(elo_d)) / 400.0, 1.0)


def defensive_matchup(home_view, away_view, match):
    h_matches = home_view.recent_matches(10)
    a_matches = away_view.recent_matches(10)
    if len(h_matches) < 3 or len(a_matches) < 3:
        return None
    _, h_conc = home_view.get_team_goals(h_matches)
    _, a_conc = away_view.get_team_goals(a_matches)
    return max(0.0, 1.0 - float(h_conc.mean())) * max(0.0, 1.0 - float(a_conc.mean()))


def tournament_draw_rate(home_view, away_view, match):
    cat = match.get("tournament_category", None)
    if cat is None:
        return None
    h_matches = home_view.matches_by_tournament_category(cat, 30)
    if len(h_matches) < 5:
        return None
    outcomes = home_view.get_team_outcomes(h_matches)
    return float((outcomes == "D").mean())


def neutral_draw_rate(home_view, away_view, match):
    h_neutral = home_view.matches_by_venue("neutral", 20)
    a_neutral = away_view.matches_by_venue("neutral", 20)
    combined = pd.concat([h_neutral, a_neutral]).drop_duplicates()
    if len(combined) < 5:
        return None
    all_outcomes = []
    for _, m in combined.iterrows():
        if m["home_goals"] > m["away_goals"]:
            all_outcomes.append("H")
        elif m["home_goals"] < m["away_goals"]:
            all_outcomes.append("A")
        else:
            all_outcomes.append("D")
    return float(np.mean([o == "D" for o in all_outcomes]))


def low_scoring_matchup(home_view, away_view, match):
    h_matches = home_view.recent_matches(10)
    a_matches = away_view.recent_matches(10)
    if len(h_matches) < 3 or len(a_matches) < 3:
        return None
    h_scored, _ = home_view.get_team_goals(h_matches)
    a_scored, _ = away_view.get_team_goals(a_matches)
    return 1.0 if (float(h_scored.mean()) < 1.5 and float(a_scored.mean()) < 1.5) else 0.0


def fifa_rank_diff_factor(home_view, away_view, match):
    return match.get("fifa_rank_diff_injected", None)


def fifa_points_diff(home_view, away_view, match):
    return match.get("fifa_points_diff_injected", None)


def fifa_rank_trend_home(home_view, away_view, match):
    return match.get("fifa_rank_trend_home_injected", None)


def fifa_rank_trend_away(home_view, away_view, match):
    return match.get("fifa_rank_trend_away_injected", None)


def elo_fifa_disagreement(home_view, away_view, match):
    return match.get("elo_fifa_disagreement_injected", None)


def form_volatility_home(home_view, away_view, match):
    matches = home_view.recent_matches(10)
    if len(matches) < 3:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    scores = outcomes.map({"W": 1.0, "D": 0.5, "L": 0.0}).values
    return float(np.var(scores))


def form_volatility_away(home_view, away_view, match):
    matches = away_view.recent_matches(10)
    if len(matches) < 3:
        return None
    outcomes = away_view.get_team_outcomes(matches)
    scores = outcomes.map({"W": 1.0, "D": 0.5, "L": 0.0}).values
    return float(np.var(scores))


def _compute_streak(outcomes, target):
    count = 0
    for val in outcomes:
        if val == target:
            count += 1
        else:
            break
    return count


def win_streak_home(home_view, away_view, match):
    matches = home_view.recent_matches(20)
    if len(matches) == 0:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    return float(_compute_streak(outcomes, "W"))


def win_streak_away(home_view, away_view, match):
    matches = away_view.recent_matches(20)
    if len(matches) == 0:
        return None
    outcomes = away_view.get_team_outcomes(matches)
    return float(_compute_streak(outcomes, "W"))


def unbeaten_streak_home(home_view, away_view, match):
    matches = home_view.recent_matches(30)
    if len(matches) == 0:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    count = 0
    for val in outcomes:
        if val in ("W", "D"):
            count += 1
        else:
            break
    return float(count)


def goal_form_trend_home(home_view, away_view, match):
    matches = home_view.recent_matches(5)
    if len(matches) < 3:
        return None
    scored, _ = home_view.get_team_goals(matches)
    scored = scored.values[::-1]
    if np.std(scored) == 0:
        return 0.0
    x = np.arange(len(scored))
    slope, _, _, _, _ = stats.linregress(x, scored)
    return float(slope)


def clean_sheet_rate_home(home_view, away_view, match):
    matches = home_view.recent_matches(10)
    if len(matches) < 3:
        return None
    _, conceded = home_view.get_team_goals(matches)
    return float((conceded == 0).mean())


def comeback_rate_home(home_view, away_view, match):
    matches = home_view.recent_matches(20)
    if len(matches) < 5:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    _, conceded = home_view.get_team_goals(matches)
    conceded_mask = conceded > 0
    if conceded_mask.sum() == 0:
        return 0.0
    conceded_outcomes = outcomes[conceded_mask]
    return float(((conceded_outcomes == "W") | (conceded_outcomes == "D")).mean())


def tournament_stage_pressure(home_view, away_view, match):
    cat = match.get("tournament_category", "other")
    if cat == "world_cup":
        return 1.0
    elif cat == "continental":
        return 0.8
    elif cat == "qualification":
        return 0.6
    elif cat == "friendly":
        return 0.2
    else:
        return 0.4


def opening_match_effect(home_view, away_view, match):
    cat = match.get("tournament_category", "other")
    if cat == "friendly":
        return 0.0
    h_cat_matches = home_view.matches_by_tournament_category(cat, 5)
    if len(h_cat_matches) == 0:
        return 1.0
    last_cat_date = h_cat_matches.iloc[0]["match_date"]
    days_since = (match["match_date"] - last_cat_date).days
    return 1.0 if days_since > 60 else 0.0


def must_win_situation(home_view, away_view, match):
    cat = match.get("tournament_category", "other")
    if cat not in ("world_cup", "continental", "qualification"):
        return 0.0
    recent = home_view.recent_matches(3)
    if len(recent) < 3:
        return 0.0
    outcomes = home_view.get_team_outcomes(recent)
    form = _form_score(outcomes.tolist())
    return max(0.0, 1.0 - form * 2) if cat in ("world_cup", "continental") else 0.0


def dead_rubber(home_view, away_view, match):
    cat = match.get("tournament_category", "other")
    return 1.0 if cat == "friendly" else 0.0


def altitude_effect(home_view, away_view, match):
    high_altitude_cities = {
        "La Paz", "Quito", "Bogotá", "Cusco", "Addis Ababa",
        "Nairobi", "Mexico City", "Johannesburg", "Sofia",
    }
    city = str(match.get("city", ""))
    return 1.0 if city in high_altitude_cities else 0.0


def travel_distance_proxy(home_view, away_view, match):
    is_cross = match.get("is_cross_confederation", False)
    return 1.0 if is_cross else 0.0


def h2h_draw_rate(home_view, away_view, match):
    opponent = match.get("away_team", "")
    h2h = home_view.h2h_matches(opponent, 10)
    if len(h2h) < 2:
        return None
    outcomes = home_view.get_team_outcomes(h2h)
    return float((outcomes == "D").mean())


def h2h_avg_goals(home_view, away_view, match):
    opponent = match.get("away_team", "")
    h2h = home_view.h2h_matches(opponent, 10)
    if len(h2h) < 2:
        return None
    return float((h2h["home_goals"] + h2h["away_goals"]).mean())


def h2h_recency(home_view, away_view, match):
    opponent = match.get("away_team", "")
    h2h = home_view.h2h_matches(opponent, 5)
    if len(h2h) == 0:
        return None
    last_date = h2h.iloc[0]["match_date"]
    days = (match["match_date"] - last_date).days
    return max(0.0, 1.0 - days / 3650.0)


def recent_upset_home(home_view, away_view, match):
    recent = home_view.recent_matches(1)
    if len(recent) == 0:
        return None
    m = recent.iloc[0]
    outcome = home_view.get_team_outcomes(recent).iloc[0]
    elo_d = m.get("elo_diff", None)
    if elo_d is None or pd.isna(elo_d):
        return None
    is_home = m["home_team"] == home_view.team
    elo_diff = float(elo_d) if is_home else -float(elo_d)
    if elo_diff < -100 and outcome == "W":
        return 1.0
    elif elo_diff > 100 and outcome == "L":
        return 1.0
    return 0.0


def goal_difference_momentum(home_view, away_view, match):
    matches_h = home_view.recent_matches(5)
    matches_a = away_view.recent_matches(5)
    if len(matches_h) < 3 or len(matches_a) < 3:
        return None
    h_scored, h_conceded = home_view.get_team_goals(matches_h)
    a_scored, a_conceded = away_view.get_team_goals(matches_a)
    h_gd = (h_scored - h_conceded).values[::-1]
    a_gd = (a_scored - a_conceded).values[::-1]
    x = np.arange(len(h_gd))
    h_slope = float(stats.linregress(x, h_gd)[0]) if np.std(h_gd) > 0 else 0.0
    a_slope = float(stats.linregress(x, a_gd)[0]) if np.std(a_gd) > 0 else 0.0
    return h_slope - a_slope


def scoring_consistency(home_view, away_view, match):
    matches_h = home_view.recent_matches(10)
    matches_a = away_view.recent_matches(10)
    if len(matches_h) < 3 or len(matches_a) < 3:
        return None
    h_scored, _ = home_view.get_team_goals(matches_h)
    a_scored, _ = away_view.get_team_goals(matches_a)
    return float(a_scored.std()) - float(h_scored.std())


# 新因子注册表
NEW_FACTOR_FUNCTIONS = {
    "draw_tendency_home": draw_tendency_home,
    "draw_tendency_away": draw_tendency_away,
    "draw_tendency_diff": draw_tendency_diff,
    "elo_closeness": elo_closeness,
    "defensive_matchup": defensive_matchup,
    "tournament_draw_rate": tournament_draw_rate,
    "neutral_draw_rate": neutral_draw_rate,
    "low_scoring_matchup": low_scoring_matchup,
    "fifa_rank_diff_factor": fifa_rank_diff_factor,
    "fifa_points_diff": fifa_points_diff,
    "fifa_rank_trend_home": fifa_rank_trend_home,
    "fifa_rank_trend_away": fifa_rank_trend_away,
    "elo_fifa_disagreement": elo_fifa_disagreement,
    "form_volatility_home": form_volatility_home,
    "form_volatility_away": form_volatility_away,
    "win_streak_home": win_streak_home,
    "win_streak_away": win_streak_away,
    "unbeaten_streak_home": unbeaten_streak_home,
    "goal_form_trend_home": goal_form_trend_home,
    "clean_sheet_rate_home": clean_sheet_rate_home,
    "comeback_rate_home": comeback_rate_home,
    "tournament_stage_pressure": tournament_stage_pressure,
    "opening_match_effect": opening_match_effect,
    "must_win_situation": must_win_situation,
    "dead_rubber": dead_rubber,
    "altitude_effect": altitude_effect,
    "travel_distance_proxy": travel_distance_proxy,
    "h2h_draw_rate": h2h_draw_rate,
    "h2h_avg_goals": h2h_avg_goals,
    "h2h_recency": h2h_recency,
    "recent_upset_home": recent_upset_home,
    "goal_difference_momentum": goal_difference_momentum,
    "scoring_consistency": scoring_consistency,
}

ALL_FACTOR_FUNCTIONS = {**FACTOR_FUNCTIONS, **NEW_FACTOR_FUNCTIONS}


# ============================================================
# 增强特征验证
# ============================================================

def run_augmented_validation(
    matches: pd.DataFrame,
    feature_df: pd.DataFrame,
    factor_names: list[str],
    output_dir: Path,
) -> dict:
    """运行增强特征的验证流水线。"""
    print_section("Phase 4: 增强特征验证")

    # 合并增强因子到 feature_df
    augmented_cols = [c for c in AUGMENTED_FACTOR_COLUMNS if c in matches.columns]
    if augmented_cols:
        print(f"  发现 {len(augmented_cols)} 个增强因子列")
        # 将增强因子从 matches 合并到 feature_df
        merge_cols = ["match_id"] + [c for c in augmented_cols if c in matches.columns]
        if "match_id" in matches.columns:
            aug_df = matches[merge_cols].copy()
            feature_df = feature_df.merge(aug_df, on="match_id", how="left")
            # 更新因子名列表
            factor_names = factor_names + [c for c in augmented_cols if c not in factor_names]
            print(f"  合并后因子总数: {len(factor_names)}")
    else:
        print("  无增强因子列可用，使用原始因子")

    results = {}

    # 1. 增强因子覆盖率分析
    print("  分析增强因子覆盖率...")
    coverage_report = {}
    for col in AUGMENTED_FACTOR_COLUMNS:
        if col in feature_df.columns:
            vals = pd.to_numeric(feature_df[col], errors="coerce")
            coverage_report[col] = {
                "coverage": float(vals.notna().mean()),
                "mean": float(vals.mean()) if vals.notna().any() else None,
                "std": float(vals.std()) if vals.notna().any() else None,
            }
    results["coverage"] = coverage_report

    # 2. 模型训练与评估
    print("  训练增强模型...")
    merged = matches.merge(feature_df, on="match_id", how="left", suffixes=("_match", "_feat"))

    # 确定特征列
    feat_cols = []
    for c in feature_df.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        if feat_col in merged.columns:
            feat_cols.append(feat_col)
        elif c in merged.columns:
            feat_cols.append(c)

    merged = merged.sort_values("match_date").reset_index(drop=True)
    label_map = {"H": 0, "D": 1, "A": 2}
    train_end_dt = pd.Timestamp(TRAIN_END, tz="UTC")

    train_df = merged[merged["match_date"] <= train_end_dt]
    val_df = merged[merged["match_date"] > train_end_dt]

    if len(train_df) < 100 or len(val_df) < 50:
        print("  [WARNING] 数据不足，跳过模型训练")
        return results

    X_train = train_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_train = train_df["result"].map(label_map).values
    X_val = val_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_val = val_df["result"].map(label_map).values
    val_labels = np.array(["H" if l == 0 else "D" if l == 1 else "A" for l in y_val])

    # LightGBM
    try:
        lgb_model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
        lgb_model.fit(X_train, y_train)
        lgb_preds = lgb_model.predict(X_val)
        lgb_ev = evaluate_predictions(lgb_preds, val_labels)
        results["lightgbm_augmented"] = _eval_to_dict(lgb_ev)
        print(f"    LightGBM (增强): Brier={lgb_ev.brier_score:.4f}, "
              f"Acc={lgb_ev.accuracy:.1%}, 平局命中={lgb_ev.draw_accuracy:.1%}")
    except Exception as e:
        print(f"    [WARNING] LightGBM 训练失败: {e}")

    # Logistic Regression
    try:
        lr_model = RegularizedLogisticBaseline(C=1.0, max_iter=2000)
        lr_model.fit(X_train, y_train)
        lr_preds = lr_model.predict(X_val)
        lr_ev = evaluate_predictions(lr_preds, val_labels)
        results["logistic_augmented"] = _eval_to_dict(lr_ev)
        print(f"    Logistic (增强): Brier={lr_ev.brier_score:.4f}, "
              f"Acc={lr_ev.accuracy:.1%}, 平局命中={lr_ev.draw_accuracy:.1%}")
    except Exception as e:
        print(f"    [WARNING] Logistic 训练失败: {e}")

    # Elo 基线
    try:
        elo_model = EloLogisticBaseline()
        elo_preds = []
        for _, m in val_df.iterrows():
            p = elo_model.predict(
                elo_home=m.get("pre_match_elo_home", 1500),
                elo_away=m.get("pre_match_elo_away", 1500),
                is_neutral=m.get("is_neutral", False),
            )
            elo_preds.append([p.home_win, p.draw, p.away_win])
        elo_ev = evaluate_predictions(np.array(elo_preds), val_labels)
        results["elo_baseline"] = _eval_to_dict(elo_ev)
        print(f"    Elo 基线: Brier={elo_ev.brier_score:.4f}")
    except Exception as e:
        print(f"    [WARNING] Elo 基线计算失败: {e}")

    # 3. Walk-Forward 验证
    print("  运行 Walk-Forward 验证...")
    try:
        wf_results = _run_walk_forward(merged, feat_cols, label_map)
        results["walk_forward"] = wf_results
        if wf_results:
            avg_brier = np.mean([w["brier_score"] for w in wf_results])
            print(f"    Walk-Forward 平均 Brier: {avg_brier:.4f}")
    except Exception as e:
        print(f"    [WARNING] Walk-Forward 失败: {e}")

    # 4. 世界杯回测
    print("  运行世界杯回测...")
    try:
        wc_results = _run_world_cup_backtest(merged, feat_cols, label_map)
        results["world_cup_backtest"] = wc_results
        for year, info in wc_results.items():
            print(f"    {year} 世界杯: Brier={info.get('model_brier', 'N/A'):.4f}")
    except Exception as e:
        print(f"    [WARNING] 世界杯回测失败: {e}")

    # 5. 增强因子消融
    print("  运行增强因子消融实验...")
    try:
        abl_results = _run_augmented_ablation(merged, feat_cols, label_map)
        results["augmented_ablation"] = abl_results
        for group, info in abl_results.items():
            delta = info.get("brier_delta", 0)
            marker = "[关键]" if delta > 0.002 else ""
            print(f"    移除 {group}: ΔBrier={delta:+.4f} {marker}")
    except Exception as e:
        print(f"    [WARNING] 消融实验失败: {e}")

    save_results(results, output_dir, "augmented_validation.json")
    return results


def _run_walk_forward(merged, feat_cols, label_map) -> list[dict]:
    """Walk-Forward 验证。"""
    dates = merged["match_date"]
    start_year = dates.dt.year.min()
    initial_train_years = 8
    step_years = 1

    results = []
    start_dt = dates.min()
    current_train_end = pd.Timestamp(f"{start_year + initial_train_years - 1}-12-31", tz="UTC")
    end_dt = pd.Timestamp(VAL_END, tz="UTC")

    while current_train_end < end_dt:
        next_end = current_train_end + pd.DateOffset(years=step_years)
        next_end = min(next_end, end_dt)

        train_mask = (dates >= start_dt) & (dates <= current_train_end)
        test_mask = (dates > current_train_end) & (dates <= next_end)

        X_train = merged.loc[train_mask, feat_cols].apply(pd.to_numeric, errors="coerce").values
        y_train = merged.loc[train_mask, "result"].map(label_map).values
        X_test = merged.loc[test_mask, feat_cols].apply(pd.to_numeric, errors="coerce").values
        y_test = merged.loc[test_mask, "result"].map(label_map).values

        if len(X_train) < 100 or len(X_test) < 10:
            current_train_end = next_end
            continue

        try:
            model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
            model.fit(X_train, y_train)
            preds = model.predict(X_test)
            test_labels = np.array(["H" if l == 0 else "D" if l == 1 else "A" for l in y_test])
            ev = evaluate_predictions(preds, test_labels)
            results.append({
                "train_end": str(current_train_end.date()),
                "test_end": str(next_end.date()),
                "brier_score": ev.brier_score,
                "accuracy": ev.accuracy,
                "draw_accuracy": ev.draw_accuracy,
            })
        except Exception:
            pass

        current_train_end = next_end

    return results


def _run_world_cup_backtest(merged, feat_cols, label_map) -> dict:
    """世界杯回测。"""
    results = {}
    for year in WORLD_CUP_YEARS:
        wc_mask = (
            (merged["tournament_category"] == "world_cup")
            & (merged["match_date"].dt.year == year)
        )
        wc_df = merged[wc_mask]
        if len(wc_df) < 5:
            continue

        wc_start = wc_df["match_date"].min()
        train_mask = merged["match_date"] < wc_start
        train_df = merged[train_mask]
        if len(train_df) < 200:
            continue

        X_train = train_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
        y_train = train_df["result"].map(label_map).values
        X_test = wc_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
        y_test = wc_df["result"].map(label_map).values

        try:
            model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
            model.fit(X_train, y_train)
            preds = model.predict(X_test)
            test_labels = np.array(["H" if l == 0 else "D" if l == 1 else "A" for l in y_test])
            ev = evaluate_predictions(preds, test_labels)

            elo_model = EloLogisticBaseline()
            baseline_preds = []
            for _, m in wc_df.iterrows():
                p = elo_model.predict(
                    elo_home=m.get("pre_match_elo_home", 1500),
                    elo_away=m.get("pre_match_elo_away", 1500),
                    is_neutral=m.get("is_neutral", False),
                )
                baseline_preds.append([p.home_win, p.draw, p.away_win])
            baseline_ev = evaluate_predictions(np.array(baseline_preds), test_labels)

            results[str(year)] = {
                "n_test": int(len(wc_df)),
                "model_brier": ev.brier_score,
                "model_accuracy": ev.accuracy,
                "model_draw_accuracy": ev.draw_accuracy,
                "baseline_brier": baseline_ev.brier_score,
                "brier_improvement": float(baseline_ev.brier_score - ev.brier_score),
            }
        except Exception:
            pass

    return results


def _run_augmented_ablation(merged, feat_cols, label_map) -> dict:
    """增强因子消融实验。"""
    train_end_dt = pd.Timestamp(TRAIN_END, tz="UTC")
    train_df = merged[merged["match_date"] <= train_end_dt]
    val_df = merged[merged["match_date"] > train_end_dt]

    if len(train_df) < 100 or len(val_df) < 50:
        return {}

    X_train_full = train_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_train = train_df["result"].map(label_map).values
    X_val_full = val_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_val = val_df["result"].map(label_map).values

    # 完整模型
    full_model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
    full_model.fit(X_train_full, y_train)
    full_preds = full_model.predict(X_val_full)
    val_labels = np.array(["H" if l == 0 else "D" if l == 1 else "A" for l in y_val])
    full_ev = evaluate_predictions(full_preds, val_labels)

    ablation_results = {}
    for group_name, group_factors in AUGMENTED_FACTOR_GROUPS.items():
        # 找到该组因子在 feat_cols 中的索引
        group_cols = []
        for fn in group_factors:
            feat_col = f"{fn}_feat"
            if feat_col in feat_cols:
                group_cols.append(feat_col)
            elif fn in feat_cols:
                group_cols.append(fn)

        if not group_cols:
            continue

        ablated_cols = [c for c in feat_cols if c not in group_cols]
        if not ablated_cols:
            continue

        col_indices = [feat_cols.index(c) for c in ablated_cols]
        X_train_abl = X_train_full[:, col_indices]
        X_val_abl = X_val_full[:, col_indices]

        try:
            abl_model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
            abl_model.fit(X_train_abl, y_train)
            abl_preds = abl_model.predict(X_val_abl)
            abl_ev = evaluate_predictions(abl_preds, val_labels)
            brier_delta = abl_ev.brier_score - full_ev.brier_score
            ablation_results[group_name] = {
                "factors_removed": group_factors,
                "brier_score": abl_ev.brier_score,
                "brier_delta": float(brier_delta),
                "is_critical": brier_delta > 0.002,
            }
        except Exception:
            pass

    return ablation_results


# ============================================================
# 与 deep_mining 结果对比
# ============================================================

def compare_with_deep_mining(
    augmented_results: dict,
    output_dir: Path,
) -> dict:
    """与 deep_mining 结果对比。"""
    print_section("Phase 5: 与 deep_mining 结果对比")

    comparison = {}

    # 尝试加载 deep_mining 结果
    dm_model_path = DEEP_MINING_DIR / "model_comparison.json"
    dm_ablation_path = DEEP_MINING_DIR / "ablation_results.json"
    dm_wc_path = DEEP_MINING_DIR / "world_cup_backtest.json"

    if dm_model_path.exists():
        dm_models = load_json(dm_model_path)
        comparison["deep_mining_models"] = dm_models.get("models", {})

        # 对比 Brier
        aug_lgb = augmented_results.get("lightgbm_augmented", {})
        dm_lgb = dm_models.get("models", {}).get("LightGBM", {})

        if aug_lgb and dm_lgb:
            aug_brier = aug_lgb.get("brier_score", None)
            dm_brier = dm_lgb.get("brier_score", None)
            if aug_brier is not None and dm_brier is not None:
                delta = dm_brier - aug_brier
                comparison["brier_comparison"] = {
                    "deep_mining_lgb": dm_brier,
                    "augmented_lgb": aug_brier,
                    "delta": delta,
                    "improved": delta > 0,
                }
                print(f"  Brier 对比: deep_mining={dm_brier:.4f}, augmented={aug_brier:.4f}, "
                      f"Δ={delta:+.4f} {'[改善]' if delta > 0 else '[未改善]'}")
    else:
        print("  [INFO] 未找到 deep_mining 模型对比结果，跳过对比")

    if dm_wc_path.exists():
        dm_wc = load_json(dm_wc_path)
        comparison["deep_mining_world_cup"] = dm_wc
        aug_wc = augmented_results.get("world_cup_backtest", {})
        for year in WORLD_CUP_YEARS:
            year_str = str(year)
            if year_str in dm_wc and year_str in aug_wc:
                dm_brier = dm_wc[year_str].get("model_brier")
                aug_brier = aug_wc[year_str].get("model_brier")
                if dm_brier and aug_brier:
                    print(f"  {year} 世界杯: DM={dm_brier:.4f}, Aug={aug_brier:.4f}, "
                          f"Δ={dm_brier - aug_brier:+.4f}")

    if dm_ablation_path.exists():
        dm_abl = load_json(dm_ablation_path)
        comparison["deep_mining_ablation"] = dm_abl

    # 增强因子贡献汇总
    aug_abl = augmented_results.get("augmented_ablation", {})
    if aug_abl:
        print("\n  增强因子消融贡献:")
        for group, info in sorted(aug_abl.items(), key=lambda x: -x[1].get("brier_delta", 0)):
            delta = info.get("brier_delta", 0)
            print(f"    {group}: ΔBrier={delta:+.4f} {'[关键]' if delta > 0.002 else ''}")

    save_results(comparison, output_dir, "comparison_with_deep_mining.json")
    return comparison


# ============================================================
# 工具函数
# ============================================================

def save_results(results: dict | list, output_dir: Path, filename: str):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    serializable = _make_serializable(results)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, default=_json_default)
    print(f"  已保存: {path}")


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _eval_to_dict(ev) -> dict:
    if isinstance(ev, EvaluationResult):
        return {
            "brier_score": ev.brier_score,
            "brier_home": ev.brier_home,
            "brier_draw": ev.brier_draw,
            "brier_away": ev.brier_away,
            "log_loss": ev.log_loss,
            "accuracy": ev.accuracy,
            "n_samples": ev.n_samples,
            "home_win_accuracy": ev.home_win_accuracy,
            "draw_accuracy": ev.draw_accuracy,
            "away_win_accuracy": ev.away_win_accuracy,
            "ece": ev.ece,
        }
    return ev


def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (np.isnan(v) or np.isinf(v)) else v
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, EvaluationResult):
        return _eval_to_dict(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _make_serializable(obj):
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, EvaluationResult):
        return _eval_to_dict(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (np.isnan(v) or np.isinf(v)) else v
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    return obj


def print_section(title: str):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ============================================================
# 主流水线
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="多源数据增强研究流水线")
    parser.add_argument("--output-dir", type=str, default="outputs/augmented_research",
                        help="输出目录 (默认: outputs/augmented_research)")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="采样大小，用于快速测试")
    parser.add_argument("--skip-api", action="store_true",
                        help="跳过所有 API 调用")
    parser.add_argument("--skip-features", action="store_true",
                        help="跳过特征计算（使用缓存）")
    args = parser.parse_args()

    output_dir = PROJECT_DIR / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("多源数据增强研究流水线")
    print(f"输出目录: {output_dir}")
    print(f"采样大小: {args.sample_size or '全部'}")
    print(f"跳过 API: {args.skip_api}")
    print("=" * 70)

    # ── Phase 1: 数据加载 ──
    data = load_all_data(sample_size=args.sample_size)
    matches = data["matches"]
    fifa_rankings = data["fifa_rankings"]

    # ── Phase 2: 多源数据采集 ──
    api_result = collect_api_data(matches, skip_api=args.skip_api)
    collected_data = api_result["data"]
    sources_status = api_result["status"]

    # ── Phase 3: Elo 回放 + FIFA 注入 + 特征计算 + 增强 ──
    print_section("Phase 3: 特征计算与增强")

    # Elo 回放
    print("  计算 Elo 历史...")
    matches = replay_elo_history(matches, EloConfig())

    # FIFA 排名注入
    matches = inject_fifa_data(matches, fifa_rankings)

    # 特征计算
    if args.skip_features:
        # 尝试加载 deep_mining 缓存
        cached_path = DEEP_MINING_DIR / "feature_cache.csv"
        if cached_path.exists():
            print(f"  加载 deep_mining 缓存特征: {cached_path}")
            feature_df = pd.read_csv(cached_path)
        else:
            cached_path = output_dir / "feature_cache.csv"
            if cached_path.exists():
                print(f"  加载增强缓存特征: {cached_path}")
                feature_df = pd.read_csv(cached_path)
            else:
                print("  [WARNING] 缓存不存在，重新计算特征")
                args.skip_features = False

    if not args.skip_features:
        compute_funcs = {}
        skip_in_compute = {"fifa_rank_diff", "odds_implied_prob", "odds_movement"}
        for name, func in ALL_FACTOR_FUNCTIONS.items():
            if name in skip_in_compute:
                continue
            compute_funcs[name] = func

        target_matches = matches
        if args.sample_size is not None and len(matches) > args.sample_size:
            target_matches = matches.sample(args.sample_size, random_state=RANDOM_SEED)

        print(f"  计算特征 ({len(target_matches)} 场比赛, {len(compute_funcs)} 个因子)...")
        feature_df = compute_all_features(target_matches, matches, compute_funcs, show_progress=True)

        cache_path = output_dir / "feature_cache.csv"
        feature_df.to_csv(cache_path, index=False)
        print(f"  特征已缓存: {cache_path}")

    factor_names = [c for c in feature_df.columns if c != "match_id"]
    print(f"  原始因子数: {len(factor_names)}")

    # API 数据增强
    print("  应用 API 数据增强...")
    matches = augment_all(
        matches,
        injuries_data=collected_data.get("injuries_data"),
        odds_data=collected_data.get("odds_data"),
        weather_data=collected_data.get("weather_data"),
        xg_data=collected_data.get("xg_data"),
        coach_data=collected_data.get("coach_data"),
        lineup_data=collected_data.get("lineup_data"),
    )

    # 保存增强后的 matches
    matches.to_csv(output_dir / "augmented_matches.csv", index=False)

    # ── Phase 4: 增强特征验证 ──
    augmented_results = run_augmented_validation(
        matches, feature_df, factor_names, output_dir,
    )

    # ── Phase 5: 与 deep_mining 对比 ──
    comparison = compare_with_deep_mining(augmented_results, output_dir)

    # ── 保存数据源状态 ──
    save_results(sources_status, output_dir, "data_sources_status.json")

    # ── 完成 ──
    print_section("流水线完成")
    print(f"所有输出保存在: {output_dir}")
    print(f"  - augmented_matches.csv: 增强后的比赛数据")
    print(f"  - feature_cache.csv: 特征缓存")
    print(f"  - augmented_validation.json: 增强验证结果")
    print(f"  - comparison_with_deep_mining.json: 与 deep_mining 对比")
    print(f"  - data_sources_status.json: 数据源可用性状态")
    print()
    print("数据源使用情况:")
    for name, status in sources_status.items():
        icon = "✓" if status == "available" else "✗" if "no_" in status else "?" if status == "skipped" else "!"
        print(f"  {icon} {name}: {status}")


if __name__ == "__main__":
    main()
