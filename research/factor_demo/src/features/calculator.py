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
        # 简化：用对手Elo差值作为权重（如果有）
        scores = outcomes.map({"W": 1.0, "D": 0.5, "L": 0.0}).values
        # 如果有对手Elo信息，用Elo差值加权；否则退化为普通得分率
        opp_elo = []
        for _, m in matches.iterrows():
            opponent = m["away_team"] if m["home_team"] == view.team else m["home_team"]
            opp_elo.append(m.get(f"opponent_elo_{opponent}", 1500.0))
        
        weights = np.array(opp_elo) / 1500.0  # 归一化
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
    
    # 用全局平均作为基准（简化：1.3球/场）
    baseline = 1.3
    
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
    
    baseline = 1.1
    
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
    
    if not is_neutral:
        # 非中立场，主队有主场优势
        return 1.0
    
    # 中立场但比赛在主队国家进行
    if home_country and home_team and home_country == home_team:
        return 0.5
    
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
    """淘汰赛经验差值（简化版：大赛场次作为代理）"""
    # 简化：用世界杯和洲际杯场次作为淘汰赛经验的代理
    return tournament_experience(home_view, away_view, match)


# ============================================================
# 洲际因子
# ============================================================

def inter_confederation_form(home_view: MatchView, away_view: MatchView, match) -> float | None:
    """同洲/跨洲表现差值"""
    is_cross = match.get("is_cross_confederation", False)
    if not is_cross:
        return 0.0  # 同洲比赛，此因子为0
    
    # 跨洲比赛：主队跨洲表现 - 客队跨洲表现
    home_matches = home_view.recent_matches(20)
    away_matches = away_view.recent_matches(20)
    
    if len(home_matches) == 0 or len(away_matches) == 0:
        return None
    
    # 简化：用整体近期状态作为代理
    home_outcomes = home_view.get_team_outcomes(home_matches).tolist()
    away_outcomes = away_view.get_team_outcomes(away_matches).tolist()
    
    return _form_score(home_outcomes) - _form_score(away_outcomes)


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
