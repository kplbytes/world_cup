"""特征增强模块

从多源 API 数据中提取新因子，注入到比赛 DataFrame 中。
所有新因子严格遵守 as-of 原则，只使用赛前可用数据。

新增因子维度:
  - 伤病因子: key_player_injured, injury_count, injury_impact_score
  - 赔率因子: odds_implied_prob, odds_margin, odds_draw_signal, odds_upset_potential
  - 天气因子: temperature, humidity, wind_speed, extreme_heat/cold, rain, weather_discomfort
  - xG 因子: pre_match_xg_diff, xg_overperformance
  - 教练因子: coach_win_rate, coach_tenure, coach_tournament_experience
  - 阵容因子: avg_age, key_players_available
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# 伤病因子
# ============================================================

# 位置重要性权重（简化版：守门员和核心进攻球员权重更高）
POSITION_WEIGHTS: dict[str, float] = {
    "Goalkeeper": 0.8,
    "Defender": 0.6,
    "Midfielder": 0.7,
    "Attacker": 0.9,
    "Forward": 0.9,
    # 中文映射
    "守门员": 0.8,
    "后卫": 0.6,
    "中场": 0.7,
    "前锋": 0.9,
}


def augment_with_injuries(
    matches: pd.DataFrame,
    injuries_data: list[dict],
) -> pd.DataFrame:
    """添加伤病因子。

    新增列:
      - key_player_injured_home: 1 如果首发球员受伤
      - key_player_injured_away: 1 如果首发球员受伤
      - injury_count_home: 受伤球员数
      - injury_count_away: 受伤球员数
      - injury_impact_score_home: 按位置重要性加权的伤病影响分
      - injury_impact_score_away: 按位置重要性加权的伤病影响分

    Args:
        matches: 比赛 DataFrame
        injuries_data: 伤病数据列表，每项包含 fixture_id, team, player, position, reason 等

    Returns:
        增强后的比赛 DataFrame
    """
    if not injuries_data:
        logger.info("无伤病数据，添加空伤病因子列")
        for col in ["key_player_injured_home", "key_player_injured_away",
                     "injury_count_home", "injury_count_away",
                     "injury_impact_score_home", "injury_impact_score_away"]:
            matches[col] = np.nan
        return matches

    matches = matches.copy()

    # 构建伤病索引: {(date, team_name): [injury_info, ...]}
    injury_index: dict[tuple, list[dict]] = {}
    for inj in injuries_data:
        date = inj.get("date", "")
        team = inj.get("team", "")
        key = (str(date), str(team))
        if key not in injury_index:
            injury_index[key] = []
        injury_index[key].append(inj)

    # 初始化新列
    matches["key_player_injured_home"] = 0
    matches["key_player_injured_away"] = 0
    matches["injury_count_home"] = 0
    matches["injury_count_away"] = 0
    matches["injury_impact_score_home"] = 0.0
    matches["injury_impact_score_away"] = 0.0

    for idx, row in matches.iterrows():
        match_date = str(row["match_date"].date()) if hasattr(row["match_date"], "date") else str(row["match_date"])[:10]
        home_team = row["home_team"]
        away_team = row["away_team"]

        for side, team in [("home", home_team), ("away", away_team)]:
            key = (match_date, team)
            injuries = injury_index.get(key, [])

            count = len(injuries)
            impact = 0.0
            key_injured = False

            for inj in injuries:
                position = inj.get("position", "")
                weight = POSITION_WEIGHTS.get(position, 0.5)
                impact += weight
                if weight >= 0.7:  # 中场及以上位置视为关键球员
                    key_injured = True

            matches.at[idx, f"injury_count_{side}"] = count
            matches.at[idx, f"key_player_injured_{side}"] = 1 if key_injured else 0
            matches.at[idx, f"injury_impact_score_{side}"] = impact

    coverage_h = (matches["injury_count_home"] > 0).mean()
    coverage_a = (matches["injury_count_away"] > 0).mean()
    logger.info(f"伤病因子覆盖率: home={coverage_h:.1%}, away={coverage_a:.1%}")

    return matches


# ============================================================
# 赔率因子
# ============================================================

def augment_with_odds(
    matches: pd.DataFrame,
    odds_data: list[dict],
) -> pd.DataFrame:
    """添加赔率因子。

    新增列:
      - odds_implied_home: 主胜隐含概率（去除利润率）
      - odds_implied_draw: 平局隐含概率
      - odds_implied_away: 客胜隐含概率
      - odds_margin: 庄家利润率 (overround - 1)
      - odds_draw_signal: 赔率隐含平局概率 vs 模型平局概率差异
      - odds_favor_home: 1 如果赔率看好主队
      - odds_upset_potential: 赔率与 Elo 的分歧程度

    Args:
        matches: 比赛 DataFrame
        odds_data: 赔率数据列表，每项包含 date, home_team, away_team,
                   odds_home, odds_draw, odds_away

    Returns:
        增强后的比赛 DataFrame
    """
    matches = matches.copy()

    if not odds_data:
        logger.info("无赔率数据，添加空赔率因子列")
        for col in ["odds_implied_home", "odds_implied_draw", "odds_implied_away",
                     "odds_margin", "odds_draw_signal", "odds_favor_home",
                     "odds_upset_potential"]:
            matches[col] = np.nan
        return matches

    # 构建赔率索引: {(date, home_team, away_team): odds_info}
    odds_index: dict[tuple, dict] = {}
    for odd in odds_data:
        date = str(odd.get("date", odd.get("match_date", "")))[:10]
        key = (date, str(odd.get("home_team", "")), str(odd.get("away_team", "")))
        odds_index[key] = odd

    # 初始化
    matches["odds_implied_home"] = np.nan
    matches["odds_implied_draw"] = np.nan
    matches["odds_implied_away"] = np.nan
    matches["odds_margin"] = np.nan
    matches["odds_draw_signal"] = np.nan
    matches["odds_favor_home"] = np.nan
    matches["odds_upset_potential"] = np.nan

    # Elo 模型默认平局概率
    DEFAULT_MODEL_DRAW = 0.26

    matched = 0
    for idx, row in matches.iterrows():
        match_date = str(row["match_date"].date()) if hasattr(row["match_date"], "date") else str(row["match_date"])[:10]
        key = (match_date, row["home_team"], row["away_team"])
        odd = odds_index.get(key)

        if odd is None:
            continue

        matched += 1

        try:
            odds_h = float(odd["odds_home"])
            odds_d = float(odd["odds_draw"])
            odds_a = float(odd["odds_away"])
        except (KeyError, ValueError, TypeError):
            continue

        if odds_h <= 0 or odds_d <= 0 or odds_a <= 0:
            continue

        # 隐含概率
        raw_h = 1.0 / odds_h
        raw_d = 1.0 / odds_d
        raw_a = 1.0 / odds_a
        overround = raw_h + raw_d + raw_a

        imp_h = raw_h / overround
        imp_d = raw_d / overround
        imp_a = raw_a / overround

        matches.at[idx, "odds_implied_home"] = imp_h
        matches.at[idx, "odds_implied_draw"] = imp_d
        matches.at[idx, "odds_implied_away"] = imp_a
        matches.at[idx, "odds_margin"] = overround - 1.0
        matches.at[idx, "odds_draw_signal"] = imp_d - DEFAULT_MODEL_DRAW
        matches.at[idx, "odds_favor_home"] = 1 if imp_h > imp_a else 0

        # 赔率与 Elo 分歧
        elo_diff = row.get("elo_diff", None)
        if elo_diff is not None and not pd.isna(elo_diff):
            # Elo 说主队强 (elo_diff > 0) 但赔率看好客队 (imp_a > imp_h)
            elo_signal = np.sign(float(elo_diff))
            odds_signal = np.sign(imp_h - imp_a)
            matches.at[idx, "odds_upset_potential"] = float(abs(elo_signal - odds_signal)) / 2.0

    coverage = matched / len(matches) if len(matches) > 0 else 0
    logger.info(f"赔率因子覆盖率: {coverage:.1%} ({matched}/{len(matches)})")

    return matches


# ============================================================
# 天气因子
# ============================================================

def augment_with_weather(
    matches: pd.DataFrame,
    weather_data: list[dict],
) -> pd.DataFrame:
    """添加天气因子。

    新增列:
      - temperature: 比赛温度 (°C)
      - humidity: 湿度 (%)
      - wind_speed: 风速 (km/h)
      - precipitation: 降水量 (mm)
      - extreme_heat: 1 如果温度 > 30°C
      - extreme_cold: 1 如果温度 < 5°C
      - rain: 1 如果降水量 > 0
      - weather_discomfort: 综合不适指数

    Args:
        matches: 比赛 DataFrame
        weather_data: 天气数据列表，每项包含 date, city, temperature,
                      humidity, wind_speed, precipitation, weather_code

    Returns:
        增强后的比赛 DataFrame
    """
    matches = matches.copy()

    if not weather_data:
        logger.info("无天气数据，添加空天气因子列")
        for col in ["temperature", "humidity", "wind_speed", "precipitation",
                     "extreme_heat", "extreme_cold", "rain", "weather_discomfort"]:
            matches[col] = np.nan
        return matches

    # 构建天气索引: {(date, city): weather_info}
    weather_index: dict[tuple, dict] = {}
    for w in weather_data:
        date = str(w.get("date", ""))[:10]
        city = str(w.get("city", ""))
        key = (date, city)
        weather_index[key] = w

    # 初始化
    matches["temperature"] = np.nan
    matches["humidity"] = np.nan
    matches["wind_speed"] = np.nan
    matches["precipitation"] = np.nan
    matches["extreme_heat"] = np.nan
    matches["extreme_cold"] = np.nan
    matches["rain"] = np.nan
    matches["weather_discomfort"] = np.nan

    matched = 0
    for idx, row in matches.iterrows():
        match_date = str(row["match_date"].date()) if hasattr(row["match_date"], "date") else str(row["match_date"])[:10]
        city = str(row.get("city", row.get("country", "")))

        w = weather_index.get((match_date, city))
        if w is None:
            continue

        matched += 1

        temp = w.get("temperature")
        hum = w.get("humidity")
        wind = w.get("wind_speed")
        precip = w.get("precipitation")

        if temp is not None:
            matches.at[idx, "temperature"] = float(temp)
            matches.at[idx, "extreme_heat"] = 1 if float(temp) > 30 else 0
            matches.at[idx, "extreme_cold"] = 1 if float(temp) < 5 else 0

        if hum is not None:
            matches.at[idx, "humidity"] = float(hum)

        if wind is not None:
            matches.at[idx, "wind_speed"] = float(wind)

        if precip is not None:
            matches.at[idx, "precipitation"] = float(precip)
            matches.at[idx, "rain"] = 1 if float(precip) > 0 else 0

        # 综合不适指数: 温度偏离舒适区 + 湿度 + 风速 + 降水
        discomfort = 0.0
        if temp is not None:
            # 舒适温度约 18°C，偏离越多越不适
            discomfort += min(abs(float(temp) - 18.0) / 15.0, 1.0)
        if hum is not None:
            discomfort += min(float(hum) / 100.0, 1.0) * 0.3
        if wind is not None:
            discomfort += min(float(wind) / 40.0, 1.0) * 0.2
        if precip is not None:
            discomfort += min(float(precip) / 10.0, 1.0) * 0.3
        matches.at[idx, "weather_discomfort"] = discomfort

    coverage = matched / len(matches) if len(matches) > 0 else 0
    logger.info(f"天气因子覆盖率: {coverage:.1%} ({matched}/{len(matches)})")

    return matches


# ============================================================
# xG 因子
# ============================================================

def augment_with_xg(
    matches: pd.DataFrame,
    xg_data: dict,
) -> pd.DataFrame:
    """添加 xG 因子（来自 StatsBomb）。

    新增列:
      - pre_match_xg_diff_home: 主队近期平均 xG 创造差值
      - pre_match_xga_diff_home: 主队近期平均 xG 失球差值
      - xg_overperformance_home: 主队实际进球 vs xG 比率
      - pre_match_xg_diff_away: 客队近期平均 xG 创造差值
      - pre_match_xga_diff_away: 客队近期平均 xG 失球差值
      - xg_overperformance_away: 客队实际进球 vs xG 比率

    Args:
        matches: 比赛 DataFrame
        xg_data: xG 数据字典，格式:
            {team_name: {
                "matches": [
                    {"date": "2022-11-20", "xg_for": 1.5, "xg_against": 0.8,
                     "goals_for": 2, "goals_against": 0},
                    ...
                ]
            }}

    Returns:
        增强后的比赛 DataFrame
    """
    matches = matches.copy()

    if not xg_data:
        logger.info("无 xG 数据，添加空 xG 因子列")
        for col in ["pre_match_xg_diff_home", "pre_match_xga_diff_home",
                     "xg_overperformance_home",
                     "pre_match_xg_diff_away", "pre_match_xga_diff_away",
                     "xg_overperformance_away"]:
            matches[col] = np.nan
        return matches

    # 初始化
    matches["pre_match_xg_diff_home"] = np.nan
    matches["pre_match_xga_diff_home"] = np.nan
    matches["xg_overperformance_home"] = np.nan
    matches["pre_match_xg_diff_away"] = np.nan
    matches["pre_match_xga_diff_away"] = np.nan
    matches["xg_overperformance_away"] = np.nan

    matched = 0
    for idx, row in matches.iterrows():
        match_date = row["match_date"]
        if hasattr(match_date, "date"):
            match_date_str = match_date.strftime("%Y-%m-%d")
        else:
            match_date_str = str(match_date)[:10]

        for side in ["home", "away"]:
            team = row[f"{side}_team"]
            team_xg = xg_data.get(team)
            if team_xg is None:
                continue

            team_matches = team_xg.get("matches", [])
            if not team_matches:
                continue

            # as-of: 只使用比赛日之前的数据
            prior_matches = [
                m for m in team_matches
                if str(m.get("date", "")) < match_date_str
            ]

            if len(prior_matches) < 3:
                continue

            # 取最近 5 场
            recent = prior_matches[-5:]

            xg_for_vals = [float(m.get("xg_for", 0)) for m in recent]
            xg_against_vals = [float(m.get("xg_against", 0)) for m in recent]
            goals_for_vals = [float(m.get("goals_for", 0)) for m in recent]

            avg_xg_for = np.mean(xg_for_vals)
            avg_xg_against = np.mean(xg_against_vals)
            total_goals = sum(goals_for_vals)
            total_xg = sum(xg_for_vals)

            matches.at[idx, f"pre_match_xg_diff_{side}"] = avg_xg_for - avg_xg_against
            matches.at[idx, f"pre_match_xga_diff_{side}"] = avg_xg_against

            if total_xg > 0:
                matches.at[idx, f"xg_overperformance_{side}"] = total_goals / total_xg

        if pd.notna(matches.at[idx, "pre_match_xg_diff_home"]) or pd.notna(matches.at[idx, "pre_match_xg_diff_away"]):
            matched += 1

    coverage = matched / len(matches) if len(matches) > 0 else 0
    logger.info(f"xG 因子覆盖率: {coverage:.1%} ({matched}/{len(matches)})")

    return matches


# ============================================================
# 教练因子
# ============================================================

def augment_with_coach(
    matches: pd.DataFrame,
    coach_data: dict,
) -> pd.DataFrame:
    """添加教练因子。

    新增列:
      - coach_win_rate_home: 主队教练历史胜率
      - coach_win_rate_away: 客队教练历史胜率
      - coach_tenure_home: 主队教练任期天数
      - coach_tenure_away: 客队教练任期天数
      - coach_tournament_experience_home: 主队教练是否有大赛经验 (0/1)
      - coach_tournament_experience_away: 客队教练是否有大赛经验 (0/1)

    Args:
        matches: 比赛 DataFrame
        coach_data: 教练数据字典，格式:
            {team_name: {
                "coach_name": "...",
                "appointed": "2020-01-15",
                "win_rate": 0.58,
                "tournament_experience": True,
            }}

    Returns:
        增强后的比赛 DataFrame
    """
    matches = matches.copy()

    if not coach_data:
        logger.info("无教练数据，添加空教练因子列")
        for col in ["coach_win_rate_home", "coach_win_rate_away",
                     "coach_tenure_home", "coach_tenure_away",
                     "coach_tournament_experience_home",
                     "coach_tournament_experience_away"]:
            matches[col] = np.nan
        return matches

    # 初始化
    matches["coach_win_rate_home"] = np.nan
    matches["coach_win_rate_away"] = np.nan
    matches["coach_tenure_home"] = np.nan
    matches["coach_tenure_away"] = np.nan
    matches["coach_tournament_experience_home"] = np.nan
    matches["coach_tournament_experience_away"] = np.nan

    matched = 0
    for idx, row in matches.iterrows():
        match_date = row["match_date"]
        if hasattr(match_date, "date"):
            match_date_dt = match_date.to_pydatetime().replace(tzinfo=None)
        else:
            match_date_dt = pd.Timestamp(match_date).to_pydatetime()

        for side in ["home", "away"]:
            team = row[f"{side}_team"]
            info = coach_data.get(team)
            if info is None:
                continue

            # 胜率
            win_rate = info.get("win_rate")
            if win_rate is not None:
                matches.at[idx, f"coach_win_rate_{side}"] = float(win_rate)

            # 任期天数（as-of: 只在任命日期早于比赛日时计算）
            appointed = info.get("appointed")
            if appointed is not None:
                try:
                    appointed_dt = pd.Timestamp(appointed).to_pydatetime()
                    tenure_days = (match_date_dt - appointed_dt).days
                    if tenure_days >= 0:
                        matches.at[idx, f"coach_tenure_{side}"] = float(tenure_days)
                except Exception:
                    pass

            # 大赛经验
            exp = info.get("tournament_experience")
            if exp is not None:
                matches.at[idx, f"coach_tournament_experience_{side}"] = 1 if exp else 0

        if pd.notna(matches.at[idx, "coach_win_rate_home"]) or pd.notna(matches.at[idx, "coach_win_rate_away"]):
            matched += 1

    coverage = matched / len(matches) if len(matches) > 0 else 0
    logger.info(f"教练因子覆盖率: {coverage:.1%} ({matched}/{len(matches)})")

    return matches


# ============================================================
# 阵容因子
# ============================================================

def augment_with_lineup_strength(
    matches: pd.DataFrame,
    lineup_data: dict,
) -> pd.DataFrame:
    """添加阵容因子。

    新增列:
      - avg_age_home: 主队首发阵容平均年龄
      - avg_age_away: 客队首发阵容平均年龄
      - key_players_available_home: 主队关键球员可用比例
      - key_players_available_away: 客队关键球员可用比例

    Args:
        matches: 比赛 DataFrame
        lineup_data: 阵容数据字典，格式:
            {(date, team_name): {
                "avg_age": 27.5,
                "key_players_available": 0.85,
                "starting_xi": [...],
            }}

    Returns:
        增强后的比赛 DataFrame
    """
    matches = matches.copy()

    if not lineup_data:
        logger.info("无阵容数据，添加空阵容因子列")
        for col in ["avg_age_home", "avg_age_away",
                     "key_players_available_home",
                     "key_players_available_away"]:
            matches[col] = np.nan
        return matches

    # 初始化
    matches["avg_age_home"] = np.nan
    matches["avg_age_away"] = np.nan
    matches["key_players_available_home"] = np.nan
    matches["key_players_available_away"] = np.nan

    matched = 0
    for idx, row in matches.iterrows():
        match_date = str(row["match_date"].date()) if hasattr(row["match_date"], "date") else str(row["match_date"])[:10]

        for side in ["home", "away"]:
            team = row[f"{side}_team"]
            key = (match_date, team)
            info = lineup_data.get(key)
            if info is None:
                continue

            avg_age = info.get("avg_age")
            if avg_age is not None:
                matches.at[idx, f"avg_age_{side}"] = float(avg_age)

            kpa = info.get("key_players_available")
            if kpa is not None:
                matches.at[idx, f"key_players_available_{side}"] = float(kpa)

        if pd.notna(matches.at[idx, "avg_age_home"]) or pd.notna(matches.at[idx, "avg_age_away"]):
            matched += 1

    coverage = matched / len(matches) if len(matches) > 0 else 0
    logger.info(f"阵容因子覆盖率: {coverage:.1%} ({matched}/{len(matches)})")

    return matches


# ============================================================
# 综合增强入口
# ============================================================

# 所有新增因子列名
AUGMENTED_FACTOR_COLUMNS = [
    # 伤病
    "key_player_injured_home", "key_player_injured_away",
    "injury_count_home", "injury_count_away",
    "injury_impact_score_home", "injury_impact_score_away",
    # 赔率
    "odds_implied_home", "odds_implied_draw", "odds_implied_away",
    "odds_margin", "odds_draw_signal", "odds_favor_home", "odds_upset_potential",
    # 天气
    "temperature", "humidity", "wind_speed", "precipitation",
    "extreme_heat", "extreme_cold", "rain", "weather_discomfort",
    # xG
    "pre_match_xg_diff_home", "pre_match_xga_diff_home", "xg_overperformance_home",
    "pre_match_xg_diff_away", "pre_match_xga_diff_away", "xg_overperformance_away",
    # 教练
    "coach_win_rate_home", "coach_win_rate_away",
    "coach_tenure_home", "coach_tenure_away",
    "coach_tournament_experience_home", "coach_tournament_experience_away",
    # 阵容
    "avg_age_home", "avg_age_away",
    "key_players_available_home", "key_players_available_away",
]

# 因子分组（用于消融实验）
AUGMENTED_FACTOR_GROUPS = {
    "injury": [
        "key_player_injured_home", "key_player_injured_away",
        "injury_count_home", "injury_count_away",
        "injury_impact_score_home", "injury_impact_score_away",
    ],
    "odds_enhanced": [
        "odds_implied_home", "odds_implied_draw", "odds_implied_away",
        "odds_margin", "odds_draw_signal", "odds_favor_home", "odds_upset_potential",
    ],
    "weather": [
        "temperature", "humidity", "wind_speed", "precipitation",
        "extreme_heat", "extreme_cold", "rain", "weather_discomfort",
    ],
    "xg": [
        "pre_match_xg_diff_home", "pre_match_xga_diff_home", "xg_overperformance_home",
        "pre_match_xg_diff_away", "pre_match_xga_diff_away", "xg_overperformance_away",
    ],
    "coach": [
        "coach_win_rate_home", "coach_win_rate_away",
        "coach_tenure_home", "coach_tenure_away",
        "coach_tournament_experience_home", "coach_tournament_experience_away",
    ],
    "lineup": [
        "avg_age_home", "avg_age_away",
        "key_players_available_home", "key_players_available_away",
    ],
}


def augment_all(
    matches: pd.DataFrame,
    injuries_data: list[dict] | None = None,
    odds_data: list[dict] | None = None,
    weather_data: list[dict] | None = None,
    xg_data: dict | None = None,
    coach_data: dict | None = None,
    lineup_data: dict | None = None,
) -> pd.DataFrame:
    """综合增强入口：依次添加所有可用数据源的因子。

    即使某些数据源不可用，也会添加空列，确保列结构一致。
    返回 DataFrame 包含所有 AUGMENTED_FACTOR_COLUMNS 列。

    Args:
        matches: 比赛 DataFrame
        injuries_data: 伤病数据
        odds_data: 赔率数据
        weather_data: 天气数据
        xg_data: xG 数据
        coach_data: 教练数据
        lineup_data: 阵容数据

    Returns:
        增强后的比赛 DataFrame
    """
    logger.info("开始综合特征增强...")

    if injuries_data is not None:
        matches = augment_with_injuries(matches, injuries_data)
    else:
        for col in ["key_player_injured_home", "key_player_injured_away",
                     "injury_count_home", "injury_count_away",
                     "injury_impact_score_home", "injury_impact_score_away"]:
            matches[col] = np.nan
        logger.info("跳过伤病因子（无数据）")

    if odds_data is not None:
        matches = augment_with_odds(matches, odds_data)
    else:
        for col in ["odds_implied_home", "odds_implied_draw", "odds_implied_away",
                     "odds_margin", "odds_draw_signal", "odds_favor_home",
                     "odds_upset_potential"]:
            matches[col] = np.nan
        logger.info("跳过赔率因子（无数据）")

    if weather_data is not None:
        matches = augment_with_weather(matches, weather_data)
    else:
        for col in ["temperature", "humidity", "wind_speed", "precipitation",
                     "extreme_heat", "extreme_cold", "rain", "weather_discomfort"]:
            matches[col] = np.nan
        logger.info("跳过天气因子（无数据）")

    if xg_data is not None:
        matches = augment_with_xg(matches, xg_data)
    else:
        for col in ["pre_match_xg_diff_home", "pre_match_xga_diff_home",
                     "xg_overperformance_home",
                     "pre_match_xg_diff_away", "pre_match_xga_diff_away",
                     "xg_overperformance_away"]:
            matches[col] = np.nan
        logger.info("跳过 xG 因子（无数据）")

    if coach_data is not None:
        matches = augment_with_coach(matches, coach_data)
    else:
        for col in ["coach_win_rate_home", "coach_win_rate_away",
                     "coach_tenure_home", "coach_tenure_away",
                     "coach_tournament_experience_home",
                     "coach_tournament_experience_away"]:
            matches[col] = np.nan
        logger.info("跳过教练因子（无数据）")

    if lineup_data is not None:
        matches = augment_with_lineup_strength(matches, lineup_data)
    else:
        for col in ["avg_age_home", "avg_age_away",
                     "key_players_available_home", "key_players_available_away"]:
            matches[col] = np.nan
        logger.info("跳过阵容因子（无数据）")

    # 统计增强因子覆盖率
    available_cols = [c for c in AUGMENTED_FACTOR_COLUMNS if c in matches.columns]
    coverage = matches[available_cols].notna().mean()
    avg_coverage = coverage.mean()
    logger.info(f"增强因子平均覆盖率: {avg_coverage:.1%}")
    logger.info(f"增强因子总数: {len(available_cols)}")

    return matches
