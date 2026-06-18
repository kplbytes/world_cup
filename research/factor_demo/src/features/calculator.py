"""因子计算模块

每个因子函数接收 (home_view, away_view, match) 参数，
返回该因子在主队视角的值。

所有因子必须严格遵守 as_of 原则，只使用 kickoff 前的数据。
"""

from __future__ import annotations

import numpy as np

from .as_of import MatchView


# ============================================================
# 评级因子
# ============================================================

def elo_diff(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """赛前Elo差值。需要外部预计算Elo后注入。"""
    return match.get("elo_diff", None)


def fifa_rank_diff(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """FIFA排名差值。需要外部预计算后注入。"""
    return match.get("fifa_rank_diff", None)


# ============================================================
# 状态因子
# ============================================================

def _form_score(outcomes: list[str]) -> float:
    """将比赛结果序列转为得分：W=1, D=0.5, L=0"""
    score_map = {"W": 1.0, "D": 0.5, "L": 0.0}
    return np.mean([score_map.get(o, 0.0) for o in outcomes])


def recent_form_5(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """最近5场状态得分率（主队 - 客队）"""
    home_matches = home_view.recent_matches(5)
    away_matches = away_view.recent_matches(5)
    
    if len(home_matches) == 0 or len(away_matches) == 0:
        return None
    
    home_outcomes = home_view.get_team_outcomes(home_matches).tolist()
    away_outcomes = away_view.get_team_outcomes(away_matches).tolist()
    
    return _form_score(home_outcomes) - _form_score(away_outcomes)


def recent_form_10(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """最近10场状态得分率（主队 - 客队）"""
    home_matches = home_view.recent_matches(10)
    away_matches = away_view.recent_matches(10)
    
    if len(home_matches) == 0 or len(away_matches) == 0:
        return None
    
    home_outcomes = home_view.get_team_outcomes(home_matches).tolist()
    away_outcomes = away_view.get_team_outcomes(away_matches).tolist()
    
    return _form_score(home_outcomes) - _form_score(away_outcomes)


def recent_form_5_opp_adjusted(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """对手强度修正后的近期状态(5场)"""
    home_matches = home_view.recent_matches(5)
    away_matches = away_view.recent_matches(5)

    if len(home_matches) == 0 or len(away_matches) == 0:
        return None

    def opp_adjusted_score(view: MatchView, matches) -> float:
        outcomes = view.get_team_outcomes(matches)
        scores = outcomes.map({"W": 1.0, "D": 0.5, "L": 0.0}).values
        # 使用 pre_match_elo_home/away 列获取对手赛前 Elo
        opp_elo = view.get_opponent_elo(matches).values

        weights = np.array(opp_elo, dtype=float) / 1500.0  # 归一化
        if weights.sum() == 0:
            return np.mean(scores)
        return np.average(scores, weights=weights)

    home_score = opp_adjusted_score(home_view, home_matches)
    away_score = opp_adjusted_score(away_view, away_matches)

    return home_score - away_score


# ============================================================
# 攻防因子
# ============================================================

def recent_goals_scored_5(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """近5场进球数差值（主队 - 客队）"""
    home_matches = home_view.recent_matches(5)
    away_matches = away_view.recent_matches(5)
    
    if len(home_matches) == 0 or len(away_matches) == 0:
        return None
    
    home_scored, _ = home_view.get_team_goals(home_matches)
    away_scored, _ = away_view.get_team_goals(away_matches)
    
    return float(home_scored.sum()) - float(away_scored.sum())


def recent_goals_conceded_5(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """近5场失球数差值（主队 - 客队，正值表示主队失球更多）"""
    home_matches = home_view.recent_matches(5)
    away_matches = away_view.recent_matches(5)
    
    if len(home_matches) == 0 or len(away_matches) == 0:
        return None
    
    _, home_conceded = home_view.get_team_goals(home_matches)
    _, away_conceded = away_view.get_team_goals(away_matches)
    
    return float(home_conceded.sum()) - float(away_conceded.sum())


def recent_goal_diff_5(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """近5场净胜球差值（主队 - 客队）"""
    home_matches = home_view.recent_matches(5)
    away_matches = away_view.recent_matches(5)
    
    if len(home_matches) == 0 or len(away_matches) == 0:
        return None
    
    home_scored, home_conceded = home_view.get_team_goals(home_matches)
    away_scored, away_conceded = away_view.get_team_goals(away_matches)
    
    home_gd = float(home_scored.sum()) - float(home_conceded.sum())
    away_gd = float(away_scored.sum()) - float(away_conceded.sum())
    
    return home_gd - away_gd


def _compute_avg_goals_baseline(view: MatchView) -> float:
    """从 _all_matches 计算 kickoff 前所有比赛的场均进球基线。"""
    all_before = view._all_matches[view._all_matches["match_date"] < view._kickoff]
    if len(all_before) == 0:
        return 1.3  # 退化默认值
    total_home = all_before["home_goals"].sum()
    total_away = all_before["away_goals"].sum()
    # 每场比赛产生 home_goals + away_goals 个进球，但 attack/defense 各看一端
    # 返回单端场均（= 总进球 / (2 * 场次)），即每队每场平均进球
    return (total_home + total_away) / (2.0 * len(all_before))


def attack_strength(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """进攻强度差值（主队 - 客队）"""
    home_matches = home_view.recent_matches(10)
    away_matches = away_view.recent_matches(10)

    if len(home_matches) < 3 or len(away_matches) < 3:
        return None

    home_scored, _ = home_view.get_team_goals(home_matches)
    away_scored, _ = away_view.get_team_goals(away_matches)

    home_avg = home_scored.mean()
    away_avg = away_scored.mean()

    # 用实际数据计算基线，而非硬编码
    baseline = _compute_avg_goals_baseline(home_view)

    home_strength = home_avg / baseline if baseline > 0 else 1.0
    away_strength = away_avg / baseline if baseline > 0 else 1.0

    return home_strength - away_strength


def defense_strength(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """防守强度差值（主队 - 客队，负值表示主队防守更好）"""
    home_matches = home_view.recent_matches(10)
    away_matches = away_view.recent_matches(10)

    if len(home_matches) < 3 or len(away_matches) < 3:
        return None

    _, home_conceded = home_view.get_team_goals(home_matches)
    _, away_conceded = away_view.get_team_goals(away_matches)

    home_avg = home_conceded.mean()
    away_avg = away_conceded.mean()

    # 用实际数据计算基线，而非硬编码
    baseline = _compute_avg_goals_baseline(home_view)

    home_strength = home_avg / baseline if baseline > 0 else 1.0
    away_strength = away_avg / baseline if baseline > 0 else 1.0

    return home_strength - away_strength


# ============================================================
# 场地因子
# ============================================================

def home_away_neutral_form(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """主客场/中立场表现差值"""
    is_neutral = match.get("is_neutral", False)
    
    if is_neutral:
        # 中立场：比较两队中立场表现
        home_neutral = home_view.matches_by_venue("neutral", 10)
        away_neutral = away_view.matches_by_venue("neutral", 10)
    else:
        # 主场：主队主场表现 vs 客队客场表现
        home_neutral = home_view.matches_by_venue("home", 10)
        away_neutral = away_view.matches_by_venue("away", 10)
    
    if len(home_neutral) == 0 or len(away_neutral) == 0:
        return None
    
    home_outcomes = home_view.get_team_outcomes(home_neutral).tolist()
    away_outcomes = away_view.get_team_outcomes(away_neutral).tolist()
    
    return _form_score(home_outcomes) - _form_score(away_outcomes)


def host_advantage(home_view: MatchView, away_view: MatchView, match) -> float:
    """东道主/半主场效应。1.0=东道主, 0.5=半主场, 0.0=无"""
    is_neutral = match.get("is_neutral", False)
    home_country = str(match.get("country", ""))
    home_team = match.get("home_team", "")
    away_team = match.get("away_team", "")

    if not is_neutral:
        # 非中立场，主队有主场优势
        return 1.0

    # 中立场但比赛在主队国家进行
    if home_country and home_team and home_country == home_team:
        return 0.5

    # 中立场但比赛在客队国家进行（客队获得半主场优势，对主队为负效应）
    if home_country and away_team and home_country == away_team:
        return -0.5

    return 0.0


# ============================================================
# 疲劳因子
# ============================================================

def rest_days(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """休息天数差值（主队 - 客队）"""
    home_rest = home_view.days_since_last_match()
    away_rest = away_view.days_since_last_match()
    
    if home_rest is None or away_rest is None:
        return None
    
    return float(home_rest - away_rest)


def match_density_30d(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """30天比赛密度差值（主队 - 客队）"""
    home_matches = home_view.matches_in_window(30)
    away_matches = away_view.matches_in_window(30)
    
    return float(len(home_matches) - len(away_matches))


def match_density_90d(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """90天比赛密度差值（主队 - 客队）"""
    home_matches = home_view.matches_in_window(90)
    away_matches = away_view.matches_in_window(90)
    
    return float(len(home_matches) - len(away_matches))


# ============================================================
# 经验因子
# ============================================================

def tournament_experience(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """大赛经验差值（主队 - 客队）"""
    home_wc = home_view.matches_by_tournament_category("world_cup", 50)
    home_cont = home_view.matches_by_tournament_category("continental", 50)
    
    away_wc = away_view.matches_by_tournament_category("world_cup", 50)
    away_cont = away_view.matches_by_tournament_category("continental", 50)
    
    home_exp = len(home_wc) + len(home_cont)
    away_exp = len(away_wc) + len(away_cont)
    
    if home_exp == 0 and away_exp == 0:
        return None
    
    return float(home_exp - away_exp)


def knockout_experience(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """淘汰赛经验差值（加权版：世界杯2x，洲际杯1.5x，与 tournament_experience 区分）"""
    home_wc = home_view.matches_by_tournament_category("world_cup", 50)
    home_cont = home_view.matches_by_tournament_category("continental", 50)

    away_wc = away_view.matches_by_tournament_category("world_cup", 50)
    away_cont = away_view.matches_by_tournament_category("continental", 50)

    # 加权：世界杯 2x，洲际杯 1.5x（tournament_experience 是等权 1x）
    home_exp = len(home_wc) * 2.0 + len(home_cont) * 1.5
    away_exp = len(away_wc) * 2.0 + len(away_cont) * 1.5

    if home_exp == 0.0 and away_exp == 0.0:
        return None

    return float(home_exp - away_exp)


# ============================================================
# 洲际因子
# ============================================================

def inter_confederation_form(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """跨洲表现差值：仅统计两队对阵另一洲球队的比赛表现"""
    is_cross = match.get("is_cross_confederation", False)
    if not is_cross:
        return 0.0  # 同洲比赛，此因子为0

    def cross_conf_form(view: MatchView) -> float | None:
        """计算该队对阵另一洲球队的近期胜率。"""
        recent = view.recent_matches(20)
        if len(recent) == 0:
            return None

        team_conf = None
        # 从 match 参数获取该队所属洲
        if match.get("home_team") == view.team:
            team_conf = match.get("home_confederation")
        elif match.get("away_team") == view.team:
            team_conf = match.get("away_confederation")

        if team_conf is None:
            return None

        # 筛选跨洲比赛：对手来自不同洲
        cross_matches = []
        for _, m in recent.iterrows():
            if m["home_team"] == view.team:
                opp_conf = m.get("away_confederation", "Unknown")
            else:
                opp_conf = m.get("home_confederation", "Unknown")
            if opp_conf != team_conf and opp_conf != "Unknown":
                cross_matches.append(m)

        if len(cross_matches) == 0:
            return None

        cross_df = pd.DataFrame(cross_matches)
        outcomes = view.get_team_outcomes(cross_df).tolist()
        return _form_score(outcomes)

    home_score = cross_conf_form(home_view)
    away_score = cross_conf_form(away_view)

    if home_score is None or away_score is None:
        return None

    return home_score - away_score


# ============================================================
# 交锋因子
# ============================================================

def h2h_last_5(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """历史交锋近5场得分率差值"""
    h2h = home_view.h2h_matches(match.get("away_team", ""), 5)
    
    if len(h2h) == 0:
        return None
    
    outcomes = home_view.get_team_outcomes(h2h).tolist()
    return _form_score(outcomes) - 0.5  # 相对于0.5的偏差


# ============================================================
# 市场因子
# ============================================================

def odds_implied_prob(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """赔率隐含概率。需要外部数据注入。"""
    return match.get("odds_implied_home_advantage", None)


def odds_movement(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """赔率变化。需要外部数据注入。"""
    return match.get("odds_movement", None)


# ============================================================
# 正式比赛与友谊赛分层
# ============================================================

def official_vs_friendly(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """正式比赛与友谊赛分层表现差值（主队 - 客队）"""
    def team_official_friendly_diff(view: MatchView) -> float | None:
        official = view.recent_matches(20, official_only=True)
        all_recent = view.recent_matches(20)
        friendly = all_recent[all_recent["tournament_category"] == "friendly"]
        
        if len(official) == 0 or len(friendly) == 0:
            return None
        
        official_score = _form_score(view.get_team_outcomes(official).tolist())
        friendly_score = _form_score(view.get_team_outcomes(friendly).tolist())
        
        return official_score - friendly_score
    
    home_diff = team_official_friendly_diff(home_view)
    away_diff = team_official_friendly_diff(away_view)
    
    if home_diff is None or away_diff is None:
        return None
    
    return home_diff - away_diff


# 因子函数注册表
FACTOR_FUNCTIONS = {
    "elo_diff": elo_diff,
    "fifa_rank_diff": fifa_rank_diff,
    "recent_form_5": recent_form_5,
    "recent_form_10": recent_form_10,
    "recent_form_5_opp_adjusted": recent_form_5_opp_adjusted,
    "recent_goals_scored_5": recent_goals_scored_5,
    "recent_goals_conceded_5": recent_goals_conceded_5,
    "recent_goal_diff_5": recent_goal_diff_5,
    "attack_strength": attack_strength,
    "defense_strength": defense_strength,
    "official_vs_friendly": official_vs_friendly,
    "home_away_neutral_form": home_away_neutral_form,
    "rest_days": rest_days,
    "match_density_30d": match_density_30d,
    "match_density_90d": match_density_90d,
    "tournament_experience": tournament_experience,
    "knockout_experience": knockout_experience,
    "inter_confederation_form": inter_confederation_form,
    "host_advantage": host_advantage,
    "h2h_last_5": h2h_last_5,
    "odds_implied_prob": odds_implied_prob,
    "odds_movement": odds_movement,
}
