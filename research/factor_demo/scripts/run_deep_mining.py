#!/usr/bin/env python3
"""深度因子挖掘与验证流水线

实现 35+ 新因子，特别针对平局预测弱点，融合 FIFA 排名数据，
进行多维度分析和严格验证。

用法:
    python -m scripts.run_deep_mining [--output-dir DIR] [--sample-size N] [--skip-features]
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
    evaluate_single_factor,
    walk_forward_validation,
    ablation_study,
    chronological_tournament_backtest,
)
from src.evaluation.bootstrap import bootstrap_brier_comparison, bootstrap_brier_single
from src.evaluation.calibration import (
    isotonic_calibration,
    platt_scale_calibration,
    reliability_diagram_data,
    calibration_metrics,
)
from src.evaluation.stratification import (
    stratify_by_year,
    stratify_by_neutral,
    stratify_by_strength_gap,
    cross_year_stability,
)
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.feature_selection import mutual_info_classif
from sklearn.inspection import permutation_importance
from sklearn.model_selection import TimeSeriesSplit

warnings.filterwarnings("ignore")

# ── 常量 ──
RANDOM_SEED = 42
DATA_START = "2000-01-01"
TRAIN_END = "2018-12-31"
VAL_START = "2019-01-01"
VAL_END = "2025-12-31"
WORLD_CUP_YEARS = [2010, 2014, 2018, 2022]

DATA_DIR = PROJECT_DIR.parent.parent / "data" / "external"


# ============================================================
# 数据加载与清洗
# ============================================================

def load_all_data(sample_size: int | None = None) -> dict:
    """加载并清洗所有数据源，返回统一字典。"""
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
    # 赛事分类
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
    # 标记点球大战比赛
    shootout_keys = set()
    for _, row in shootouts.iterrows():
        key = f"{row['match_date'].strftime('%Y-%m-%d')}_{row['home_team']}_{row['away_team']}"
        shootout_keys.add(key)
    results["is_shootout"] = results.apply(
        lambda r: f"{r['match_date'].strftime('%Y-%m-%d')}_{r['home_team']}_{r['away_team']}" in shootout_keys,
        axis=1,
    )
    print(f"    点球大战标记: {results['is_shootout'].sum()} 场")

    # 3. 进球数据
    goalscorers_path = DATA_DIR / "goalscorers.csv"
    print(f"  加载进球数据: {goalscorers_path}")
    goalscorers = pd.read_csv(goalscorers_path)
    goalscorers = goalscorers.rename(columns={"date": "match_date"})
    goalscorers["match_date"] = pd.to_datetime(goalscorers["match_date"], utc=True)
    # 提取赛前可用信息：每队历史进球时间分布（上半场/下半场进球比例等）
    print(f"    进球记录: {len(goalscorers)}")

    # 4. FIFA 排名
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
        "goalscorers": goalscorers,
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
    """加载 FIFA 排名数据，兼容不同列名格式。"""
    df = pd.read_csv(csv_path)

    # 兼容不同列名
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

    # 如果没有 fifa_rank 列，从每个日期的排序中计算
    if "fifa_rank" not in df.columns and "fifa_points" in df.columns and "rank_date" in df.columns:
        df["fifa_rank"] = df.groupby("rank_date")["fifa_points"].rank(ascending=False, method="min").astype("Int64")

    keep_cols = [c for c in ["team_name", "fifa_rank", "fifa_points", "rank_date", "previous_points"] if c in df.columns]
    df = df[keep_cols]

    if "rank_date" in df.columns:
        df["rank_date"] = pd.to_datetime(df["rank_date"], utc=True)
    else:
        print("    [WARNING] FIFA 排名数据缺少 rank_date 列，无法使用")
        return pd.DataFrame()

    # 队名映射
    df["team_name"] = df["team_name"].apply(lambda n: _FIFA_NAME_MAP.get(n, n))
    df = df.sort_values("rank_date").reset_index(drop=True)
    return df


# ============================================================
# 35+ 新因子定义
# ============================================================

def _form_score(outcomes) -> float:
    """将比赛结果序列转为得分：W=1, D=0.5, L=0"""
    score_map = {"W": 1.0, "D": 0.5, "L": 0.0}
    vals = [score_map.get(o, 0.0) for o in outcomes] if not isinstance(outcomes, np.ndarray) else outcomes
    return float(np.mean(vals)) if len(vals) > 0 else 0.0


# ── 平局专项因子 ──

def draw_tendency_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队近20场平局率"""
    matches = home_view.recent_matches(20)
    if len(matches) < 3:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    return float((outcomes == "D").mean())


def draw_tendency_away(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """客队近20场平局率"""
    matches = away_view.recent_matches(20)
    if len(matches) < 3:
        return None
    outcomes = away_view.get_team_outcomes(matches)
    return float((outcomes == "D").mean())


def draw_tendency_diff(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """两队平局倾向差值"""
    h = draw_tendency_home(home_view, away_view, match)
    a = draw_tendency_away(home_view, away_view, match)
    if h is None or a is None:
        return None
    return h - a


def elo_closeness(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """Elo 接近度：1 - 归一化 elo_diff，越高越接近→越可能平局"""
    elo_d = match.get("elo_diff", None)
    if elo_d is None or pd.isna(elo_d):
        return None
    return 1.0 - min(abs(float(elo_d)) / 400.0, 1.0)


def defensive_matchup(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """防守型对决：两队近期失球率都低 → 更可能平局"""
    h_matches = home_view.recent_matches(10)
    a_matches = away_view.recent_matches(10)
    if len(h_matches) < 3 or len(a_matches) < 3:
        return None
    _, h_conc = home_view.get_team_goals(h_matches)
    _, a_conc = away_view.get_team_goals(a_matches)
    h_rate = float(h_conc.mean())
    a_rate = float(a_conc.mean())
    # 两队失球率都低于1.0时，值更高
    return max(0.0, 1.0 - h_rate) * max(0.0, 1.0 - a_rate)


def tournament_draw_rate(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """该赛事类型的历史平局率"""
    cat = match.get("tournament_category", None)
    if cat is None:
        return None
    # 使用主队在该赛事类型的历史
    h_matches = home_view.matches_by_tournament_category(cat, 30)
    if len(h_matches) < 5:
        return None
    outcomes = home_view.get_team_outcomes(h_matches)
    return float((outcomes == "D").mean())


def neutral_draw_rate(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """中立场平局率"""
    h_neutral = home_view.matches_by_venue("neutral", 20)
    a_neutral = away_view.matches_by_venue("neutral", 20)
    combined = pd.concat([h_neutral, a_neutral]).drop_duplicates()
    if len(combined) < 5:
        return None
    # 计算这些中立场比赛的平局率
    all_outcomes = []
    for _, m in combined.iterrows():
        if m["home_goals"] > m["away_goals"]:
            all_outcomes.append("H")
        elif m["home_goals"] < m["away_goals"]:
            all_outcomes.append("A")
        else:
            all_outcomes.append("D")
    return float(np.mean([o == "D" for o in all_outcomes]))


def low_scoring_matchup(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """低进球对决：两队场均进球都<1.5"""
    h_matches = home_view.recent_matches(10)
    a_matches = away_view.recent_matches(10)
    if len(h_matches) < 3 or len(a_matches) < 3:
        return None
    h_scored, _ = home_view.get_team_goals(h_matches)
    a_scored, _ = away_view.get_team_goals(a_matches)
    h_avg = float(h_scored.mean())
    a_avg = float(a_scored.mean())
    # 两队都低进球时为1，否则为0
    return 1.0 if (h_avg < 1.5 and a_avg < 1.5) else 0.0


# ── FIFA 排名因子 ──

def _get_fifa_rank_at_time(fifa_rankings: pd.DataFrame, team: str, match_date, field: str = "fifa_rank"):
    """获取某队在某时间点的 FIFA 排名/积分（as-of 逻辑）。"""
    if fifa_rankings.empty or "rank_date" not in fifa_rankings.columns:
        return None
    match_dt = match_date
    if hasattr(match_dt, "tz") and match_dt.tz is not None:
        match_dt = match_dt.tz_localize(None)
    rank_dates = fifa_rankings["rank_date"].dt.tz_localize(None) if fifa_rankings["rank_date"].dt.tz is not None else fifa_rankings["rank_date"]
    prior = fifa_rankings[rank_dates <= match_dt]
    prior = prior[prior["team_name"] == team]
    if len(prior) == 0:
        return None
    latest = prior.sort_values("rank_date").iloc[-1]
    return float(latest[field]) if field in latest.index else None


def fifa_rank_diff_factor(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """FIFA 排名差值（需要外部注入）"""
    return match.get("fifa_rank_diff_injected", None)


def fifa_points_diff(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """FIFA 积分差值（需要外部注入）"""
    return match.get("fifa_points_diff_injected", None)


def fifa_rank_trend_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队近6个月排名变化趋势（需要外部注入）"""
    return match.get("fifa_rank_trend_home_injected", None)


def fifa_rank_trend_away(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """客队近6个月排名变化趋势（需要外部注入）"""
    return match.get("fifa_rank_trend_away_injected", None)


def elo_fifa_disagreement(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """Elo 与 FIFA 排名不一致度（需要外部注入）"""
    return match.get("elo_fifa_disagreement_injected", None)


# ── 增强状态因子 ──

def form_volatility_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队近10场状态波动率（结果方差）"""
    matches = home_view.recent_matches(10)
    if len(matches) < 3:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    scores = outcomes.map({"W": 1.0, "D": 0.5, "L": 0.0}).values
    return float(np.var(scores))


def form_volatility_away(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """客队近10场状态波动率"""
    matches = away_view.recent_matches(10)
    if len(matches) < 3:
        return None
    outcomes = away_view.get_team_outcomes(matches)
    scores = outcomes.map({"W": 1.0, "D": 0.5, "L": 0.0}).values
    return float(np.var(scores))


def _compute_streak(outcomes: pd.Series, target: str) -> int:
    """计算从最近一场开始的连续 target 结果次数。"""
    count = 0
    for val in outcomes:
        if val == target:
            count += 1
        else:
            break
    return count


def win_streak_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队当前连胜场次"""
    matches = home_view.recent_matches(20)
    if len(matches) == 0:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    return float(_compute_streak(outcomes, "W"))


def win_streak_away(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """客队当前连胜场次"""
    matches = away_view.recent_matches(20)
    if len(matches) == 0:
        return None
    outcomes = away_view.get_team_outcomes(matches)
    return float(_compute_streak(outcomes, "W"))


def unbeaten_streak_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队当前不败场次"""
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


def goal_form_trend_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队近5场进球趋势（线性回归斜率）"""
    matches = home_view.recent_matches(5)
    if len(matches) < 3:
        return None
    scored, _ = home_view.get_team_goals(matches)
    scored = scored.values[::-1]  # 从旧到新
    if np.std(scored) == 0:
        return 0.0
    x = np.arange(len(scored))
    slope, _, _, _, _ = stats.linregress(x, scored)
    return float(slope)


def clean_sheet_rate_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队近10场零封率"""
    matches = home_view.recent_matches(10)
    if len(matches) < 3:
        return None
    _, conceded = home_view.get_team_goals(matches)
    return float((conceded == 0).mean())


def comeback_rate_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队逆风追平/反超率（需要进球时序数据，此处用简化代理）"""
    # 简化：近20场中，先失球但最终不败的比例
    # 由于缺乏进球时序数据，使用"失球但结果为W/D"的比例作为代理
    matches = home_view.recent_matches(20)
    if len(matches) < 5:
        return None
    outcomes = home_view.get_team_outcomes(matches)
    _, conceded = home_view.get_team_goals(matches)
    # 找出有失球的比赛
    conceded_mask = conceded > 0
    if conceded_mask.sum() == 0:
        return 0.0
    conceded_outcomes = outcomes[conceded_mask]
    return float(((conceded_outcomes == "W") | (conceded_outcomes == "D")).mean())


# ── 赛事/上下文因子 ──

def tournament_stage_pressure(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """赛事阶段压力代理（淘汰赛>小组赛>友谊赛）"""
    cat = match.get("tournament_category", "other")
    is_wc = cat == "world_cup"
    is_cont = cat == "continental"
    is_qual = cat == "qualification"
    is_friendly = cat == "friendly"
    # 世界杯淘汰赛压力最高
    if is_wc:
        return 1.0
    elif is_cont:
        return 0.8
    elif is_qual:
        return 0.6
    elif is_friendly:
        return 0.2
    else:
        return 0.4


def opening_match_effect(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """赛事首场效应：两队在该赛事中是否首场比赛"""
    cat = match.get("tournament_category", "other")
    if cat == "friendly":
        return 0.0
    # 检查主队在该赛事类别中最近一场比赛是否在30天前
    h_cat_matches = home_view.matches_by_tournament_category(cat, 5)
    if len(h_cat_matches) == 0:
        return 1.0  # 无历史→首场
    last_cat_date = h_cat_matches.iloc[0]["match_date"]
    days_since = (match["match_date"] - last_cat_date).days
    return 1.0 if days_since > 60 else 0.0


def must_win_situation(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """必须赢球局面代理：小组赛后期（需要更细粒度数据，此处简化）"""
    cat = match.get("tournament_category", "other")
    if cat not in ("world_cup", "continental", "qualification"):
        return 0.0
    # 代理：该队近期状态差+大赛→更可能需要赢
    recent = home_view.recent_matches(3)
    if len(recent) < 3:
        return 0.0
    outcomes = home_view.get_team_outcomes(recent)
    form = _form_score(outcomes.tolist())
    # 状态差+大赛 = 可能需要赢
    return max(0.0, 1.0 - form * 2) if cat in ("world_cup", "continental") else 0.0


def dead_rubber(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """无关紧要比赛代理"""
    cat = match.get("tournament_category", "other")
    if cat == "friendly":
        return 1.0
    return 0.0


def altitude_effect(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """高原效应代理（基于城市数据，简化版）"""
    # 已知高原城市
    high_altitude_cities = {
        "La Paz", "Quito", "Bogotá", "Cusco", "Addis Ababa",
        "Nairobi", "Mexico City", "Johannesburg", "Sofia",
        "Denver", "Salt Lake City",
    }
    city = str(match.get("city", ""))
    return 1.0 if city in high_altitude_cities else 0.0


def travel_distance_proxy(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """跨洲旅行代理"""
    is_cross = match.get("is_cross_confederation", False)
    if not is_cross:
        return 0.0
    # 跨洲比赛，客队旅行更远
    return 1.0


# ── 交锋增强因子 ──

def h2h_draw_rate(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """历史交锋平局率"""
    opponent = match.get("away_team", "")
    h2h = home_view.h2h_matches(opponent, 10)
    if len(h2h) < 2:
        return None
    outcomes = home_view.get_team_outcomes(h2h)
    return float((outcomes == "D").mean())


def h2h_avg_goals(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """历史交锋场均总进球"""
    opponent = match.get("away_team", "")
    h2h = home_view.h2h_matches(opponent, 10)
    if len(h2h) < 2:
        return None
    total_goals = (h2h["home_goals"] + h2h["away_goals"]).mean()
    return float(total_goals)


def h2h_recency(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """最近一次交锋距今天数（归一化）"""
    opponent = match.get("away_team", "")
    h2h = home_view.h2h_matches(opponent, 5)
    if len(h2h) == 0:
        return None
    last_date = h2h.iloc[0]["match_date"]
    days = (match["match_date"] - last_date).days
    # 归一化：越近越重要
    return max(0.0, 1.0 - days / 3650.0)  # 10年衰减


# ── 动量/心理因子 ──

def recent_upset_home(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主队上一场是否为爆冷"""
    recent = home_view.recent_matches(1)
    if len(recent) == 0:
        return None
    m = recent.iloc[0]
    outcome = home_view.get_team_outcomes(recent).iloc[0]
    elo_d = m.get("elo_diff", None)
    if elo_d is None or pd.isna(elo_d):
        return None
    # 爆冷定义：Elo低但赢了，或Elo高但输了
    is_home = m["home_team"] == home_view.team
    elo_diff = float(elo_d) if is_home else -float(elo_d)
    if elo_diff < -100 and outcome == "W":
        return 1.0  # 爆冷赢
    elif elo_diff > 100 and outcome == "L":
        return 1.0  # 爆冷输
    return 0.0


def goal_difference_momentum(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """净胜球动量：近5场净胜球趋势"""
    matches_h = home_view.recent_matches(5)
    matches_a = away_view.recent_matches(5)
    if len(matches_h) < 3 or len(matches_a) < 3:
        return None
    h_scored, h_conceded = home_view.get_team_goals(matches_h)
    a_scored, a_conceded = away_view.get_team_goals(matches_a)
    h_gd = (h_scored - h_conceded).values[::-1]
    a_gd = (a_scored - a_conceded).values[::-1]
    # 线性趋势
    x = np.arange(len(h_gd))
    h_slope = float(stats.linregress(x, h_gd)[0]) if np.std(h_gd) > 0 else 0.0
    a_slope = float(stats.linregress(x, a_gd)[0]) if np.std(a_gd) > 0 else 0.0
    return h_slope - a_slope


def scoring_consistency(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """进球稳定性（近10场进球标准差的负值，越高越稳定）"""
    matches_h = home_view.recent_matches(10)
    matches_a = away_view.recent_matches(10)
    if len(matches_h) < 3 or len(matches_a) < 3:
        return None
    h_scored, _ = home_view.get_team_goals(matches_h)
    a_scored, _ = away_view.get_team_goals(matches_a)
    h_std = float(h_scored.std())
    a_std = float(a_scored.std())
    return a_std - h_std  # 正值=主队更稳定


# ── 新因子注册表 ──
NEW_FACTOR_FUNCTIONS = {
    # 平局专项
    "draw_tendency_home": draw_tendency_home,
    "draw_tendency_away": draw_tendency_away,
    "draw_tendency_diff": draw_tendency_diff,
    "elo_closeness": elo_closeness,
    "defensive_matchup": defensive_matchup,
    "tournament_draw_rate": tournament_draw_rate,
    "neutral_draw_rate": neutral_draw_rate,
    "low_scoring_matchup": low_scoring_matchup,
    # FIFA 排名
    "fifa_rank_diff_factor": fifa_rank_diff_factor,
    "fifa_points_diff": fifa_points_diff,
    "fifa_rank_trend_home": fifa_rank_trend_home,
    "fifa_rank_trend_away": fifa_rank_trend_away,
    "elo_fifa_disagreement": elo_fifa_disagreement,
    # 增强状态
    "form_volatility_home": form_volatility_home,
    "form_volatility_away": form_volatility_away,
    "win_streak_home": win_streak_home,
    "win_streak_away": win_streak_away,
    "unbeaten_streak_home": unbeaten_streak_home,
    "goal_form_trend_home": goal_form_trend_home,
    "clean_sheet_rate_home": clean_sheet_rate_home,
    "comeback_rate_home": comeback_rate_home,
    # 赛事/上下文
    "tournament_stage_pressure": tournament_stage_pressure,
    "opening_match_effect": opening_match_effect,
    "must_win_situation": must_win_situation,
    "dead_rubber": dead_rubber,
    "altitude_effect": altitude_effect,
    "travel_distance_proxy": travel_distance_proxy,
    # 交锋增强
    "h2h_draw_rate": h2h_draw_rate,
    "h2h_avg_goals": h2h_avg_goals,
    "h2h_recency": h2h_recency,
    # 动量/心理
    "recent_upset_home": recent_upset_home,
    "goal_difference_momentum": goal_difference_momentum,
    "scoring_consistency": scoring_consistency,
}

# 合并所有因子（原有21 + 新增32 = 53个）
ALL_FACTOR_FUNCTIONS = {**FACTOR_FUNCTIONS, **NEW_FACTOR_FUNCTIONS}

# 因子分组（用于消融实验）
FACTOR_GROUPS = {
    "elo": ["elo_diff"],
    "fifa_ranking": ["fifa_rank_diff_factor", "fifa_points_diff", "fifa_rank_trend_home",
                     "fifa_rank_trend_away", "elo_fifa_disagreement"],
    "draw_specific": ["draw_tendency_home", "draw_tendency_away", "draw_tendency_diff",
                      "elo_closeness", "defensive_matchup", "tournament_draw_rate",
                      "neutral_draw_rate", "low_scoring_matchup"],
    "form_basic": ["recent_form_5", "recent_form_10", "recent_form_5_opp_adjusted"],
    "form_enhanced": ["form_volatility_home", "form_volatility_away", "win_streak_home",
                      "win_streak_away", "unbeaten_streak_home", "goal_form_trend_home",
                      "clean_sheet_rate_home", "comeback_rate_home"],
    "attack_defense": ["recent_goals_scored_5", "recent_goals_conceded_5", "recent_goal_diff_5",
                       "attack_strength", "defense_strength"],
    "venue": ["home_away_neutral_form", "host_advantage"],
    "fatigue": ["rest_days", "match_density_30d", "match_density_90d"],
    "experience": ["tournament_experience", "knockout_experience"],
    "confederation": ["inter_confederation_form"],
    "context": ["tournament_stage_pressure", "opening_match_effect", "must_win_situation",
                "dead_rubber", "altitude_effect", "travel_distance_proxy"],
    "h2h": ["h2h_last_5", "h2h_draw_rate", "h2h_avg_goals", "h2h_recency"],
    "momentum": ["recent_upset_home", "goal_difference_momentum", "scoring_consistency"],
    "official_friendly": ["official_vs_friendly"],
}


# ============================================================
# FIFA 排名注入
# ============================================================

def inject_fifa_data(matches: pd.DataFrame, fifa_rankings: pd.DataFrame) -> pd.DataFrame:
    """将 FIFA 排名数据注入比赛数据（as-of 逻辑）。"""
    if fifa_rankings.empty:
        print("  [WARNING] FIFA 排名数据为空，跳过注入")
        matches["fifa_rank_diff_injected"] = np.nan
        matches["fifa_points_diff_injected"] = np.nan
        matches["fifa_rank_trend_home_injected"] = np.nan
        matches["fifa_rank_trend_away_injected"] = np.nan
        matches["elo_fifa_disagreement_injected"] = np.nan
        return matches

    print("  注入 FIFA 排名数据...")
    matches = matches.copy()

    # 预处理排名日期
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

        # 主队排名和积分
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

        # 排名趋势：6个月前的排名 vs 当前排名
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

        # Elo-FIFA 不一致度
        elo_d = row.get("elo_diff", None)
        if elo_d is not None and not np.isnan(elo_d) and not np.isnan(h_rank) and not np.isnan(a_rank):
            # Elo 说主队强（elo_diff > 0）但 FIFA 说主队弱（rank更高=数字更大）
            elo_signal = np.sign(float(elo_d))
            fifa_signal = np.sign(float(a_rank) - float(h_rank))  # rank越低越强
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
# 多维度因子分析
# ============================================================

def run_factor_analysis(
    feature_df: pd.DataFrame,
    matches: pd.DataFrame,
    factor_names: list[str],
    output_dir: Path,
) -> dict:
    """多维度因子分析：相关性、互信息、特征重要性、VIF、时间稳定性。"""
    print_section("Phase 4: 多维度因子分析")

    results = {}

    # 准备数据
    label_map = {"H": 0, "D": 1, "A": 2}
    merged = feature_df.copy()

    # 确保有 match_id 对齐
    if "match_id" in matches.columns:
        labels_df = matches[["match_id", "result"]].copy()
        merged = merged.merge(labels_df, on="match_id", how="left")
    else:
        merged["result"] = matches["result"].values[:len(merged)]

    valid = merged[merged["result"].notna()].copy()
    valid["target"] = valid["result"].map(label_map)

    # 1. 相关性矩阵
    print("  计算因子-目标相关性...")
    corr_results = {}
    for fn in factor_names:
        if fn not in valid.columns:
            continue
        vals = pd.to_numeric(valid[fn], errors="coerce")
        target = valid["target"]
        mask = vals.notna() & target.notna()
        if mask.sum() < 50:
            continue
        r, p = stats.pointbiserialr(vals[mask], target[mask])
        # 与平局的相关性
        is_draw = (valid["result"] == "D").astype(float)
        r_draw, p_draw = stats.pointbiserialr(vals[mask], is_draw[mask])
        corr_results[fn] = {
            "point_biserial_r": float(r),
            "point_biserial_p": float(p),
            "draw_correlation_r": float(r_draw),
            "draw_correlation_p": float(p_draw),
        }
    results["correlation"] = corr_results

    # 2. 互信息
    print("  计算互信息...")
    mi_results = {}
    X_cols = [fn for fn in factor_names if fn in valid.columns]
    if len(X_cols) > 0:
        X = valid[X_cols].apply(pd.to_numeric, errors="coerce").fillna(0).values
        y = valid["target"].values
        try:
            mi_scores = mutual_info_classif(X, y, random_state=RANDOM_SEED)
            for fn, mi in zip(X_cols, mi_scores):
                mi_results[fn] = float(mi)
        except Exception as e:
            print(f"    [WARNING] 互信息计算失败: {e}")
    results["mutual_information"] = mi_results

    # 3. LightGBM 特征重要性
    print("  计算 LightGBM 特征重要性...")
    lgb_importance = {}
    if len(X_cols) > 0:
        X = valid[X_cols].apply(pd.to_numeric, errors="coerce").values
        y = valid["target"].values
        mask = ~np.isnan(y)
        X, y = X[mask], y[mask]
        try:
            model = LightGBMBaseline(n_estimators=100, max_depth=5, learning_rate=0.05)
            model.fit(X, y)
            importances = model._model.feature_importances_
            for fn, imp in zip(X_cols, importances):
                lgb_importance[fn] = int(imp)
        except Exception as e:
            print(f"    [WARNING] LightGBM 特征重要性计算失败: {e}")
    results["lgb_importance"] = lgb_importance

    # 4. 置换重要性
    print("  计算置换重要性...")
    perm_importance = {}
    if len(X_cols) > 0 and len(valid) > 200:
        try:
            X = valid[X_cols].apply(pd.to_numeric, errors="coerce").values
            y = valid["target"].values
            mask = ~np.isnan(y)
            X, y = X[mask], y[mask]
            model = LightGBMBaseline(n_estimators=100, max_depth=4, learning_rate=0.05)
            model.fit(X, y)
            perm_result = permutation_importance(
                model._model, X, y, n_repeats=10, random_state=RANDOM_SEED, scoring="neg_log_loss"
            )
            for fn, imp_mean, imp_std in zip(X_cols, perm_result.importances_mean, perm_result.importances_std):
                perm_importance[fn] = {"mean": float(imp_mean), "std": float(imp_std)}
        except Exception as e:
            print(f"    [WARNING] 置换重要性计算失败: {e}")
    results["permutation_importance"] = perm_importance

    # 5. VIF（方差膨胀因子）
    print("  计算 VIF...")
    vif_results = {}
    if len(X_cols) > 1:
        try:
            from statsmodels.stats.outliers_influence import variance_inflation_factor
            X_vif = valid[X_cols].apply(pd.to_numeric, errors="coerce").fillna(0)
            # 采样以加速
            if len(X_vif) > 5000:
                X_vif = X_vif.sample(5000, random_state=RANDOM_SEED)
            X_vif_values = X_vif.values
            for i, fn in enumerate(X_cols):
                try:
                    vif = variance_inflation_factor(X_vif_values, i)
                    vif_results[fn] = float(vif)
                except Exception:
                    vif_results[fn] = float("inf")
        except ImportError:
            print("    [WARNING] statsmodels 未安装，跳过 VIF 计算")
    results["vif"] = vif_results

    # 6. 时间稳定性分析
    print("  计算时间稳定性...")
    stability_results = {}
    if "match_id" in matches.columns:
        stability_base = matches[["match_id", "match_date", "result"]].copy()
    else:
        stability_base = matches[["match_date", "result"]].copy().iloc[:len(valid)]
        stability_base = stability_base.reset_index(drop=True)

    valid_with_date = valid.copy()
    if "match_date" not in valid_with_date.columns:
        if "match_id" in valid_with_date.columns:
            date_map = matches.set_index("match_id")["match_date"]
            valid_with_date["match_date"] = valid_with_date["match_id"].map(date_map)
        else:
            valid_with_date["match_date"] = matches["match_date"].values[:len(valid)]

    valid_with_date["year"] = pd.to_datetime(valid_with_date["match_date"], utc=True).dt.year
    is_draw = (valid_with_date["result"] == "D").astype(float)

    for fn in factor_names:
        if fn not in valid_with_date.columns:
            continue
        vals = pd.to_numeric(valid_with_date[fn], errors="coerce")
        yearly_corrs = {}
        for year in sorted(valid_with_date["year"].dropna().unique()):
            year_mask = valid_with_date["year"] == year
            if year_mask.sum() < 30:
                continue
            y_vals = vals[year_mask]
            y_draw = is_draw[year_mask]
            valid_mask = y_vals.notna()
            if valid_mask.sum() < 30:
                continue
            r, _ = stats.pointbiserialr(y_vals[valid_mask], y_draw[valid_mask])
            yearly_corrs[int(year)] = float(r)

        if len(yearly_corrs) >= 3:
            corr_values = list(yearly_corrs.values())
            stability_results[fn] = {
                "yearly_correlations": yearly_corrs,
                "mean_corr": float(np.mean(corr_values)),
                "std_corr": float(np.std(corr_values)),
                "direction_stability": float(np.mean(np.sign(corr_values) == np.sign(np.mean(corr_values)))),
                "cv": float(np.std(corr_values) / abs(np.mean(corr_values))) if abs(np.mean(corr_values)) > 0.001 else float("inf"),
            }
    results["time_stability"] = stability_results

    save_results(results, output_dir, "factor_analysis_report.json")
    return results


# ============================================================
# IC 分析
# ============================================================

def compute_ic_analysis(
    feature_df: pd.DataFrame,
    matches: pd.DataFrame,
    factor_names: list[str],
) -> dict:
    """计算 Information Coefficient (IC) 和 ICIR。"""
    print("  计算 IC / ICIR...")

    label_map = {"H": 0, "D": 1, "A": 2}
    merged = feature_df.copy()

    if "match_id" in matches.columns:
        labels_df = matches[["match_id", "match_date", "result"]].copy()
        merged = merged.merge(labels_df, on="match_id", how="left")
    else:
        merged["match_date"] = matches["match_date"].values[:len(merged)]
        merged["result"] = matches["result"].values[:len(merged)]

    valid = merged[merged["result"].notna()].copy()
    valid["year"] = pd.to_datetime(valid["match_date"], utc=True).dt.year

    # 将结果转为数值：H=1, D=0, A=-1（主队视角的"收益"）
    valid["return"] = valid["result"].map({"H": 1.0, "D": 0.0, "A": -1.0})

    ic_results = {}
    for fn in factor_names:
        if fn not in valid.columns:
            continue
        vals = pd.to_numeric(valid[fn], errors="coerce")
        returns = valid["return"]

        # 全局 IC（Spearman rank correlation）
        mask = vals.notna() & returns.notna()
        if mask.sum() < 50:
            continue

        global_ic, _ = stats.spearmanr(vals[mask], returns[mask])

        # 年度 IC
        yearly_ics = {}
        for year in sorted(valid["year"].dropna().unique()):
            year_mask = (valid["year"] == year) & mask
            if year_mask.sum() < 30:
                continue
            ic, _ = stats.spearmanr(vals[year_mask], returns[year_mask])
            yearly_ics[int(year)] = float(ic)

        ic_values = list(yearly_ics.values()) if yearly_ics else [global_ic]
        mean_ic = float(np.mean(ic_values))
        std_ic = float(np.std(ic_values))
        icir = mean_ic / std_ic if std_ic > 0 else 0.0

        # 方向稳定性
        if len(ic_values) > 1:
            dir_stability = float(np.mean(np.sign(ic_values) == np.sign(mean_ic)))
        else:
            dir_stability = 1.0

        ic_results[fn] = {
            "global_ic": float(global_ic),
            "mean_ic": mean_ic,
            "std_ic": std_ic,
            "icir": icir,
            "direction_stability": dir_stability,
            "yearly_ics": yearly_ics,
            "coverage": float(mask.sum() / len(valid)),
        }

    return ic_results


# ============================================================
# Walk-Forward 验证
# ============================================================

def run_walk_forward(
    matches: pd.DataFrame,
    feature_df: pd.DataFrame,
    factor_names: list[str],
    output_dir: Path,
) -> dict:
    """Walk-Forward 验证（扩展窗口）。"""
    print_section("Phase 5: Walk-Forward 验证")

    # 合并特征
    merged, feat_cols = merge_features(matches, feature_df, factor_names)
    merged = merged.sort_values("match_date").reset_index(drop=True)

    # 准备数据
    label_map = {"H": 0, "D": 1, "A": 2}
    X = merged[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y = merged["result"].map(label_map).values
    dates = merged["match_date"]

    # 时间分割
    start_year = dates.dt.year.min()
    initial_train_years = 8
    step_years = 1

    results = []
    current_train_end = pd.Timestamp(f"{start_year + initial_train_years - 1}-12-31", tz="UTC")
    end_dt = pd.Timestamp(VAL_END, tz="UTC")
    start_dt = dates.min()

    while current_train_end < end_dt:
        next_end = current_train_end + pd.DateOffset(years=step_years)
        next_end = min(next_end, end_dt)

        train_mask = (dates >= start_dt) & (dates <= current_train_end)
        test_mask = (dates > current_train_end) & (dates <= next_end)

        X_train, y_train = X[train_mask], y[train_mask]
        X_test, y_test = X[test_mask], y[test_mask]

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
                "n_train": int(len(X_train)),
                "n_test": int(len(X_test)),
                "brier_score": ev.brier_score,
                "log_loss": ev.log_loss,
                "accuracy": ev.accuracy,
                "draw_accuracy": ev.draw_accuracy,
            })
        except Exception as e:
            print(f"    [WARNING] Walk-forward 窗口 {current_train_end.date()} 失败: {e}")

        current_train_end = next_end

    print(f"  完成 {len(results)} 个 walk-forward 窗口")
    save_results(results, output_dir, "walk_forward_results.json")
    return results


# ============================================================
# 世界杯时间序列回测
# ============================================================

def run_world_cup_backtest(
    matches: pd.DataFrame,
    feature_df: pd.DataFrame,
    factor_names: list[str],
    output_dir: Path,
) -> dict:
    """按时间顺序对世界杯进行回测。"""
    print_section("Phase 6: 世界杯时间序列回测")

    merged, feat_cols = merge_features(matches, feature_df, factor_names)
    merged = merged.sort_values("match_date").reset_index(drop=True)

    label_map = {"H": 0, "D": 1, "A": 2}
    results = {}

    for year in WORLD_CUP_YEARS:
        # 世界杯比赛
        wc_mask = (
            (merged["tournament_category"] == "world_cup")
            & (merged["match_date"].dt.year == year)
        )
        wc_df = merged[wc_mask]

        if len(wc_df) < 5:
            print(f"  {year} 世界杯: 比赛数不足 ({len(wc_df)})")
            continue

        # 训练集：该届之前所有数据
        wc_start = wc_df["match_date"].min()
        train_mask = merged["match_date"] < wc_start
        train_df = merged[train_mask]

        if len(train_df) < 200:
            print(f"  {year} 世界杯: 训练数据不足 ({len(train_df)})")
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

            # 基线对比
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
                "n_train": int(len(train_df)),
                "model_brier": ev.brier_score,
                "model_log_loss": ev.log_loss,
                "model_accuracy": ev.accuracy,
                "model_draw_accuracy": ev.draw_accuracy,
                "baseline_brier": baseline_ev.brier_score,
                "baseline_accuracy": baseline_ev.accuracy,
                "brier_improvement": float(baseline_ev.brier_score - ev.brier_score),
            }
            print(f"  {year} 世界杯: Brier={ev.brier_score:.4f} (基线={baseline_ev.brier_score:.4f}, "
                  f"改善={baseline_ev.brier_score - ev.brier_score:+.4f}), 平局命中率={ev.draw_accuracy:.1%}")
        except Exception as e:
            print(f"  {year} 世界杯: 回测失败 - {e}")

    save_results(results, output_dir, "world_cup_backtest.json")
    return results


# ============================================================
# 消融实验
# ============================================================

def run_ablation_study(
    matches: pd.DataFrame,
    feature_df: pd.DataFrame,
    factor_names: list[str],
    output_dir: Path,
) -> dict:
    """消融实验：逐个移除因子组。"""
    print_section("Phase 7: 消融实验")

    merged, feat_cols = merge_features(matches, feature_df, factor_names)
    merged = merged.sort_values("match_date").reset_index(drop=True)

    label_map = {"H": 0, "D": 1, "A": 2}
    train_end_dt = pd.Timestamp(TRAIN_END, tz="UTC")

    train_df = merged[merged["match_date"] <= train_end_dt]
    val_df = merged[merged["match_date"] > train_end_dt]

    if len(train_df) < 100 or len(val_df) < 50:
        print("  [WARNING] 数据不足，跳过消融实验")
        return {}

    X_train_full = train_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_train = train_df["result"].map(label_map).values
    X_val_full = val_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_val = val_df["result"].map(label_map).values

    # 完整模型
    print("  训练完整模型...")
    full_model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
    full_model.fit(X_train_full, y_train)
    full_preds = full_model.predict(X_val_full)
    val_labels = np.array(["H" if l == 0 else "D" if l == 1 else "A" for l in y_val])
    full_ev = evaluate_predictions(full_preds, val_labels)
    print(f"    完整模型 Brier: {full_ev.brier_score:.4f}")

    # 逐组消融
    ablation_results = {}
    for group_name, group_factors in FACTOR_GROUPS.items():
        # 找出该组因子在 feat_cols 中的索引
        group_cols = []
        for fn in group_factors:
            feat_col = f"{fn}_feat"
            if feat_col in feat_cols:
                group_cols.append(feat_col)
            elif fn in feat_cols:
                group_cols.append(fn)

        if not group_cols:
            continue

        # 移除该组后的特征列
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
                "log_loss": abl_ev.log_loss,
                "accuracy": abl_ev.accuracy,
                "draw_accuracy": abl_ev.draw_accuracy,
                "is_critical": brier_delta > 0.002,
            }
            print(f"    移除 {group_name}: ΔBrier={brier_delta:+.4f} {'[关键]' if brier_delta > 0.002 else ''}")
        except Exception as e:
            print(f"    移除 {group_name}: 失败 - {e}")

    save_results(ablation_results, output_dir, "ablation_results.json")
    return ablation_results


# ============================================================
# 校准分析
# ============================================================

def run_calibration_analysis(
    matches: pd.DataFrame,
    feature_df: pd.DataFrame,
    factor_names: list[str],
    output_dir: Path,
) -> dict:
    """概率校准分析。"""
    print_section("Phase 8: 概率校准分析")

    merged, feat_cols = merge_features(matches, feature_df, factor_names)
    merged = merged.sort_values("match_date").reset_index(drop=True)

    label_map = {"H": 0, "D": 1, "A": 2}
    train_end_dt = pd.Timestamp(TRAIN_END, tz="UTC")

    train_df = merged[merged["match_date"] <= train_end_dt]
    val_df = merged[merged["match_date"] > train_end_dt]

    if len(train_df) < 100 or len(val_df) < 50:
        print("  [WARNING] 数据不足，跳过校准分析")
        return {}

    X_train = train_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_train = train_df["result"].map(label_map).values
    X_val = val_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_val = val_df["result"].map(label_map).values

    # 原始模型
    model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
    model.fit(X_train, y_train)
    raw_preds = model.predict(X_val)

    # 等距回归校准（用训练集拟合校准器，再用验证集预测）
    print("  应用等距回归校准...")
    raw_train_preds = model.predict(X_train)
    calibrated_preds = isotonic_calibration(raw_train_preds, y_train, raw_preds)

    val_labels = np.array(["H" if l == 0 else "D" if l == 1 else "A" for l in y_val])

    raw_ev = evaluate_predictions(raw_preds, val_labels)
    cal_ev = evaluate_predictions(calibrated_preds, val_labels)

    # 可靠性图数据
    reliability_data = reliability_diagram_data(raw_preds, val_labels)
    cal_reliability_data = reliability_diagram_data(calibrated_preds, val_labels)

    # Brier Skill Score vs 基线
    baseline_preds = []
    elo_model = EloLogisticBaseline()
    for _, m in val_df.iterrows():
        p = elo_model.predict(
            elo_home=m.get("pre_match_elo_home", 1500),
            elo_away=m.get("pre_match_elo_away", 1500),
            is_neutral=m.get("is_neutral", False),
        )
        baseline_preds.append([p.home_win, p.draw, p.away_win])
    baseline_preds = np.array(baseline_preds)

    label_map_oh = {"H": 0, "D": 1, "A": 2}
    y_true_onehot = np.zeros((len(val_labels), 3))
    for i, l in enumerate(val_labels):
        y_true_onehot[i, label_map_oh[l]] = 1.0

    bss_raw = brier_skill_score(y_true_onehot, raw_preds, baseline_preds)
    bss_cal = brier_skill_score(y_true_onehot, calibrated_preds, baseline_preds)

    results = {
        "raw": {
            "brier_score": raw_ev.brier_score,
            "brier_draw": raw_ev.brier_draw,
            "log_loss": raw_ev.log_loss,
            "accuracy": raw_ev.accuracy,
            "draw_accuracy": raw_ev.draw_accuracy,
            "ece": raw_ev.ece,
        },
        "calibrated_isotonic": {
            "brier_score": cal_ev.brier_score,
            "brier_draw": cal_ev.brier_draw,
            "log_loss": cal_ev.log_loss,
            "accuracy": cal_ev.accuracy,
            "draw_accuracy": cal_ev.draw_accuracy,
            "ece": cal_ev.ece,
        },
        "brier_skill_score_vs_elo": {
            "raw": float(bss_raw),
            "calibrated": float(bss_cal),
        },
        "reliability_raw": {k: {kk: vv for kk, vv in v.items()} for k, v in reliability_data.items()},
        "reliability_calibrated": {k: {kk: vv for kk, vv in v.items()} for k, v in cal_reliability_data.items()},
    }

    print(f"  原始 Brier: {raw_ev.brier_score:.4f}, 校准后: {cal_ev.brier_score:.4f}")
    print(f"  原始平局 ECE: {raw_ev.ece:.4f}, 校准后: {cal_ev.ece:.4f}")
    print(f"  BSS vs Elo: 原始={bss_raw:.4f}, 校准后={bss_cal:.4f}")

    save_results(results, output_dir, "calibration_results.json")
    return results


# ============================================================
# 模型对比
# ============================================================

def run_model_comparison(
    matches: pd.DataFrame,
    feature_df: pd.DataFrame,
    factor_names: list[str],
    output_dir: Path,
) -> dict:
    """全模型对比。"""
    print_section("Phase 9: 模型对比")

    merged, feat_cols = merge_features(matches, feature_df, factor_names)
    merged = merged.sort_values("match_date").reset_index(drop=True)

    label_map = {"H": 0, "D": 1, "A": 2}
    train_end_dt = pd.Timestamp(TRAIN_END, tz="UTC")

    train_df = merged[merged["match_date"] <= train_end_dt]
    val_df = merged[merged["match_date"] > train_end_dt]

    if len(train_df) < 100 or len(val_df) < 50:
        print("  [WARNING] 数据不足，跳过模型对比")
        return {}

    X_train = train_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_train = train_df["result"].map(label_map).values
    X_val = val_df[feat_cols].apply(pd.to_numeric, errors="coerce").values
    y_val = val_df["result"].map(label_map).values
    val_labels = np.array(["H" if l == 0 else "D" if l == 1 else "A" for l in y_val])

    all_results = {}

    # 1. HomeFixed
    home_model = HomeFixedBaseline.from_data(train_df)
    preds = np.tile([home_model.home_win_rate, home_model.draw_rate, home_model.away_win_rate], (len(val_df), 1))
    all_results["HomeFixed"] = evaluate_predictions(preds, val_labels)

    # 2. Frequency
    freq_model = FrequencyBaseline().fit(train_df)
    preds = []
    for _, m in val_df.iterrows():
        p = freq_model.predict(m)
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["Frequency"] = evaluate_predictions(np.array(preds), val_labels)

    # 3. EloLogistic
    elo_model = EloLogisticBaseline()
    preds = []
    for _, m in val_df.iterrows():
        p = elo_model.predict(
            elo_home=m.get("pre_match_elo_home", 1500),
            elo_away=m.get("pre_match_elo_away", 1500),
            is_neutral=m.get("is_neutral", False),
        )
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["EloLogistic"] = evaluate_predictions(np.array(preds), val_labels)

    # 4. EloPoisson
    poisson_model = EloPoissonBaseline()
    preds = []
    for _, m in val_df.iterrows():
        p = poisson_model.predict(
            elo_home=m.get("pre_match_elo_home", 1500),
            elo_away=m.get("pre_match_elo_away", 1500),
            is_neutral=m.get("is_neutral", False),
        )
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["EloPoisson"] = evaluate_predictions(np.array(preds), val_labels)

    # 5. Logistic Regression (原有因子)
    lr_model = RegularizedLogisticBaseline(C=1.0, max_iter=2000)
    lr_model.fit(X_train, y_train)
    preds = lr_model.predict(X_val)
    all_results["LogisticRegression"] = evaluate_predictions(preds, val_labels)

    # 6. LightGBM (原有因子)
    lgb_model = LightGBMBaseline(n_estimators=200, max_depth=5, learning_rate=0.05)
    lgb_model.fit(X_train, y_train)
    preds = lgb_model.predict(X_val)
    all_results["LightGBM"] = evaluate_predictions(preds, val_labels)

    # 打印对比
    print(compare_models(all_results))

    # Bootstrap 对比: LightGBM vs EloLogistic
    elo_preds = []
    for _, m in val_df.iterrows():
        p = elo_model.predict(
            elo_home=m.get("pre_match_elo_home", 1500),
            elo_away=m.get("pre_match_elo_away", 1500),
            is_neutral=m.get("is_neutral", False),
        )
        elo_preds.append([p.home_win, p.draw, p.away_win])

    lgb_preds = lgb_model.predict(X_val)
    boot_result = bootstrap_brier_comparison(
        np.array(elo_preds), lgb_preds, val_labels, n_bootstrap=1000
    )
    print(f"\n  LightGBM vs EloLogistic Bootstrap:")
    print(f"    Brier 差异: {boot_result['brier_diff_full']:+.4f}")
    print(f"    95% CI: [{boot_result['ci_lower']:+.4f}, {boot_result['ci_upper']:+.4f}]")
    print(f"    显著: {boot_result['ci_significant']}")

    results = {
        "models": {name: _eval_to_dict(ev) for name, ev in all_results.items()},
        "bootstrap_lgb_vs_elo": boot_result,
    }

    save_results(results, output_dir, "model_comparison.json")
    return results


# ============================================================
# 因子候选评估
# ============================================================

def evaluate_factor_candidates(
    feature_df: pd.DataFrame,
    matches: pd.DataFrame,
    factor_names: list[str],
    factor_analysis: dict,
    ic_analysis: dict,
    ablation_results: dict,
    baseline_brier: float,
) -> dict:
    """评估每个因子候选，生成最终准入决策。"""
    print_section("Phase 10: 因子候选评估")

    candidates = {}

    for fn in factor_names:
        if fn not in feature_df.columns:
            continue

        vals = pd.to_numeric(feature_df[fn], errors="coerce")
        coverage = float(vals.notna().mean())
        mean_val = float(vals.mean()) if vals.notna().any() else None
        std_val = float(vals.std()) if vals.notna().any() else None

        # 相关性
        corr_info = factor_analysis.get("correlation", {}).get(fn, {})
        mi_score = factor_analysis.get("mutual_information", {}).get(fn, 0.0)
        lgb_imp = factor_analysis.get("lgb_importance", {}).get(fn, 0)
        perm_imp = factor_analysis.get("permutation_importance", {}).get(fn, {})
        vif_val = factor_analysis.get("vif", {}).get(fn, None)
        stability_info = factor_analysis.get("time_stability", {}).get(fn, {})

        # IC 信息
        ic_info = ic_analysis.get(fn, {})

        # 消融贡献
        ablation_delta = None
        for group_name, group_info in ablation_results.items():
            if fn in FACTOR_GROUPS.get(group_name, []):
                ablation_delta = group_info.get("brier_delta", None)
                break

        # 方向稳定性
        dir_stability = compute_factor_direction_stability(
            vals.values,
            matches["result"].values[:len(vals)],
        )

        # 判断准入
        is_redundant = vif_val is not None and vif_val > 10
        brier_improvement = ablation_delta is not None and ablation_delta > 0.002
        has_coverage = coverage >= 0.3
        has_stability = stability_info.get("direction_stability", 0) >= 0.6 if stability_info else False
        ic_significant = abs(ic_info.get("mean_ic", 0)) > 0.01 if ic_info else False

        if is_redundant:
            decision = "rejected"
            reason = f"VIF={vif_val:.1f} > 10，与其他因子高度共线"
        elif not has_coverage:
            decision = "needs_more_data"
            reason = f"覆盖率不足 ({coverage:.1%})"
        elif brier_improvement:
            decision = "promoted"
            reason = f"消融正向贡献 (ΔBrier={ablation_delta:+.4f})"
        elif ic_significant and has_stability:
            decision = "candidate"
            reason = f"IC显著 ({ic_info.get('mean_ic', 0):.4f}) 且方向稳定"
        else:
            decision = "rejected"
            reason = "贡献不显著或方向不稳定"

        candidates[fn] = {
            "coverage_rate": coverage,
            "mean": mean_val,
            "std": std_val,
            "correlation": corr_info,
            "mutual_information": mi_score,
            "lgb_importance": lgb_imp,
            "permutation_importance": perm_imp,
            "vif": vif_val,
            "time_stability": stability_info,
            "ic": ic_info,
            "ablation_brier_delta": ablation_delta,
            "direction_stability": dir_stability,
            "decision": decision,
            "reason": reason,
        }

        print(f"  {fn}: {decision} - {reason}")

    return candidates


# ============================================================
# 生成准入决策文档
# ============================================================

def generate_promotion_document(
    candidates: dict,
    output_dir: Path,
) -> None:
    """生成 PROMOTION_DECISION.md 文档。"""
    print_section("Phase 11: 生成准入决策文档")

    lines = []
    lines.append("# 因子准入决策报告")
    lines.append("")
    lines.append(f"生成时间: {datetime.now().isoformat()}")
    lines.append("")

    # 统计
    promoted = [n for n, c in candidates.items() if c["decision"] == "promoted"]
    candidate = [n for n, c in candidates.items() if c["decision"] == "candidate"]
    rejected = [n for n, c in candidates.items() if c["decision"] == "rejected"]
    needs_data = [n for n, c in candidates.items() if c["decision"] == "needs_more_data"]

    lines.append("## 摘要")
    lines.append("")
    lines.append(f"- **准入 (Promoted)**: {len(promoted)} 个")
    lines.append(f"- **候选 (Candidate)**: {len(candidate)} 个")
    lines.append(f"- **拒绝 (Rejected)**: {len(rejected)} 个")
    lines.append(f"- **数据不足 (Needs More Data)**: {len(needs_data)} 个")
    lines.append("")

    # 准入因子详情
    lines.append("## 准入因子")
    lines.append("")
    for fn in promoted:
        c = candidates[fn]
        lines.append(f"### {fn}")
        lines.append(f"- 覆盖率: {c['coverage_rate']:.1%}")
        lines.append(f"- 消融 ΔBrier: {c.get('ablation_brier_delta', 'N/A')}")
        lines.append(f"- IC: {c.get('ic', {}).get('mean_ic', 'N/A')}")
        lines.append(f"- ICIR: {c.get('ic', {}).get('icir', 'N/A')}")
        lines.append(f"- 方向稳定性: {c.get('direction_stability', {}).get('consistency', 'N/A')}")
        lines.append(f"- 原因: {c['reason']}")
        lines.append("")

    # 候选因子
    lines.append("## 候选因子")
    lines.append("")
    for fn in candidate:
        c = candidates[fn]
        lines.append(f"- **{fn}**: {c['reason']}")
    lines.append("")

    # 拒绝因子
    lines.append("## 拒绝因子")
    lines.append("")
    for fn in rejected:
        c = candidates[fn]
        lines.append(f"- **{fn}**: {c['reason']}")
    lines.append("")

    # 平局专项因子评估
    lines.append("## 平局专项因子评估")
    lines.append("")
    draw_factors = [n for n in candidates if "draw" in n.lower() or n in
                    ["elo_closeness", "defensive_matchup", "low_scoring_matchup"]]
    for fn in draw_factors:
        c = candidates[fn]
        draw_corr = c.get("correlation", {}).get("draw_correlation_r", "N/A")
        lines.append(f"- **{fn}**: 决策={c['decision']}, 平局相关性={draw_corr}, 原因={c['reason']}")
    lines.append("")

    output_path = output_dir / "PROMOTION_DECISION.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  已保存: {output_path}")


# ============================================================
# 工具函数
# ============================================================

def merge_features(df_slice: pd.DataFrame, feature_df: pd.DataFrame, factor_names: list[str]) -> tuple:
    """将特征合并回比赛数据。"""
    merged = df_slice.merge(feature_df, on="match_id", how="left", suffixes=("_match", "_feat"))

    feature_cols = []
    for c in feature_df.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        if feat_col in merged.columns:
            feature_cols.append(feat_col)
        elif c in merged.columns:
            feature_cols.append(c)
    return merged, feature_cols


def save_results(results: dict | list, output_dir: Path, filename: str):
    """保存结果到 JSON。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    serializable = _make_serializable(results)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, default=_json_default)
    print(f"  已保存: {path}")


def _eval_to_dict(ev) -> dict:
    """将 EvaluationResult 转为可序列化字典。"""
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
    """JSON 序列化回退。"""
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
    if isinstance(obj, EvaluationResult):
        return _eval_to_dict(obj)
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _make_serializable(obj):
    """递归地将对象转为可 JSON 序列化的结构。"""
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
    if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
        return None
    return obj


def print_section(title: str):
    """打印格式化的章节标题。"""
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ============================================================
# 主流水线
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="深度因子挖掘与验证流水线")
    parser.add_argument("--output-dir", type=str, default="outputs/deep_mining",
                        help="输出目录 (默认: outputs/deep_mining)")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="采样大小，用于快速测试")
    parser.add_argument("--skip-features", action="store_true",
                        help="跳过特征计算（使用缓存）")
    args = parser.parse_args()

    output_dir = PROJECT_DIR / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("深度因子挖掘与验证流水线")
    print(f"输出目录: {output_dir}")
    print(f"采样大小: {args.sample_size or '全部'}")
    print("=" * 70)

    # ── Phase 1: 数据加载 ──
    data = load_all_data(sample_size=args.sample_size)
    matches = data["matches"]
    fifa_rankings = data["fifa_rankings"]

    # ── Phase 2: Elo 回放 ──
    print_section("Phase 2: Elo 回放")
    matches = replay_elo_history(matches, EloConfig())
    print(f"  Elo 回放完成: {len(matches)} 场比赛")

    # ── Phase 3: FIFA 排名注入 + 特征计算 ──
    print_section("Phase 3: FIFA 排名注入 + 特征计算")

    # 注入 FIFA 排名
    matches = inject_fifa_data(matches, fifa_rankings)

    # 确定计算目标
    target_matches = matches
    if args.sample_size is not None and len(matches) > args.sample_size:
        target_matches = matches.sample(args.sample_size, random_state=RANDOM_SEED)

    # 合并因子函数（排除需要外部注入的 FIFA 因子，它们已通过 inject 处理）
    compute_funcs = {}
    skip_in_compute = {"fifa_rank_diff", "odds_implied_prob", "odds_movement"}
    for name, func in ALL_FACTOR_FUNCTIONS.items():
        if name in skip_in_compute:
            continue
        compute_funcs[name] = func

    # 计算特征
    if args.skip_features:
        cached_path = output_dir / "feature_cache.csv"
        if cached_path.exists():
            print(f"  加载缓存特征: {cached_path}")
            feature_df = pd.read_csv(cached_path)
        else:
            print("  [WARNING] 缓存不存在，重新计算特征")
            args.skip_features = False

    if not args.skip_features:
        print(f"  计算特征 ({len(target_matches)} 场比赛, {len(compute_funcs)} 个因子)...")
        feature_df = compute_all_features(target_matches, matches, compute_funcs, show_progress=True)

        # 缓存
        cache_path = output_dir / "feature_cache.csv"
        feature_df.to_csv(cache_path, index=False)
        print(f"  特征已缓存: {cache_path}")

    # 收集所有因子名
    factor_names = [c for c in feature_df.columns if c != "match_id"]
    print(f"  计算完成: {len(factor_names)} 个因子, {len(feature_df)} 条记录")

    # ── Phase 4: 多维度因子分析 ──
    factor_analysis = run_factor_analysis(feature_df, matches, factor_names, output_dir)

    # IC 分析
    ic_analysis = compute_ic_analysis(feature_df, matches, factor_names)
    save_results(ic_analysis, output_dir, "ic_analysis.json")

    # ── Phase 5: Walk-Forward 验证 ──
    wf_results = run_walk_forward(matches, feature_df, factor_names, output_dir)

    # ── Phase 6: 世界杯回测 ──
    wc_results = run_world_cup_backtest(matches, feature_df, factor_names, output_dir)

    # ── Phase 7: 消融实验 ──
    abl_results = run_ablation_study(matches, feature_df, factor_names, output_dir)

    # ── Phase 8: 校准分析 ──
    cal_results = run_calibration_analysis(matches, feature_df, factor_names, output_dir)

    # ── Phase 9: 模型对比 ──
    model_results = run_model_comparison(matches, feature_df, factor_names, output_dir)

    # ── Phase 10: 因子候选评估 ──
    # 获取基线 Brier
    baseline_brier = 0.55  # 默认值
    if model_results and "models" in model_results:
        elo_brier = model_results["models"].get("EloLogistic", {}).get("brier_score")
        if elo_brier is not None:
            baseline_brier = elo_brier

    candidates = evaluate_factor_candidates(
        feature_df, matches, factor_names,
        factor_analysis, ic_analysis, abl_results,
        baseline_brier,
    )
    save_results(candidates, output_dir, "factor_candidates.json")

    # ── Phase 11: 生成准入决策文档 ──
    generate_promotion_document(candidates, output_dir)

    # ── 完成 ──
    print_section("流水线完成")
    print(f"所有输出保存在: {output_dir}")
    print(f"  - factor_analysis_report.json: 因子分析报告")
    print(f"  - ic_analysis.json: IC 分析")
    print(f"  - model_comparison.json: 模型对比")
    print(f"  - walk_forward_results.json: Walk-Forward 验证")
    print(f"  - world_cup_backtest.json: 世界杯回测")
    print(f"  - ablation_results.json: 消融实验")
    print(f"  - calibration_results.json: 校准分析")
    print(f"  - factor_candidates.json: 因子候选")
    print(f"  - PROMOTION_DECISION.md: 准入决策")


if __name__ == "__main__":
    main()
