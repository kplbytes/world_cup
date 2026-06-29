"""Matchup style analyzer — predicts how two teams' tactical styles will interact.

Uses TeamProfile.tactical_style_tags_json and rhythm metrics (over_2_5_rate,
under_2_5_rate, both_teams_score_rate, etc.) to produce:
  - matchup_label: a short label like "开放对攻战" / "防守绞杀战"
  - expected_total_goals: qualitative expectation (low/moderate/high)
  - btts_probability: both-teams-to-score tendency
  - draw_tendency: how likely a draw is given both teams' styles
  - narrative: 2-3 sentence human-readable description of the matchup

This is a rule-based engine (no ML) that leverages existing profile data.
The output is purely additive — it does NOT modify Poisson probabilities.
"""

from __future__ import annotations

from typing import Any

from app.models import TeamProfile


def _tags(profile: TeamProfile | None) -> set[str]:
    if profile is None:
        return set()
    return set(profile.tactical_style_tags_json or [])


def _metric(profile: TeamProfile | None, attr: str, default: float = 0.0) -> float:
    if profile is None:
        return default
    return float(getattr(profile, attr, default) or default)


def analyze_matchup(
    home_profile: TeamProfile | None,
    away_profile: TeamProfile | None,
) -> dict[str, Any]:
    """Analyze the tactical matchup between two teams.

    Returns a dict with matchup_label, expected_total_goals, btts_probability,
    draw_tendency, pace_label, key_factors, and narrative.
    """
    home_tags = _tags(home_profile)
    away_tags = _tags(away_profile)
    all_tags = home_tags | away_tags

    # --- Determine matchup archetype ---
    home_open = "开放对攻型" in home_tags or "强压制型" in home_tags
    away_open = "开放对攻型" in away_tags or "强压制型" in away_tags
    home_defensive = "保守低比分型" in home_tags or "防守反击型" in home_tags or "小比分倾向" in home_tags
    away_defensive = "保守低比分型" in away_tags or "防守反击型" in away_tags or "小比分倾向" in away_tags

    if home_open and away_open:
        archetype = "open_exchange"
        matchup_label = "开放对攻战"
        pace_label = "快节奏对攻"
    elif home_defensive and away_defensive:
        archetype = "defensive_grind"
        matchup_label = "防守绞杀战"
        pace_label = "慢节奏防守"
    elif (home_open and away_defensive) or (home_defensive and away_open):
        archetype = "attack_vs_defense"
        matchup_label = "攻防对决"
        pace_label = "攻防转换"
    elif "慢热型" in all_tags:
        archetype = "slow_start"
        matchup_label = "试探型对决"
        pace_label = "慢热开局"
    else:
        archetype = "balanced"
        matchup_label = "均衡较量"
        pace_label = "常规节奏"

    # --- Quantitative expectations ---
    home_over = _metric(home_profile, "over_2_5_rate", 0.45)
    away_over = _metric(away_profile, "over_2_5_rate", 0.45)
    home_under = _metric(home_profile, "under_2_5_rate", 0.45)
    away_under = _metric(away_profile, "under_2_5_rate", 0.45)
    home_bts = _metric(home_profile, "both_teams_score_rate", 0.50)
    away_bts = _metric(away_profile, "both_teams_score_rate", 0.50)
    home_draw = _metric(home_profile, "draw_rate_overall", 0.25)
    away_draw = _metric(away_profile, "draw_rate_overall", 0.25)

    avg_over = (home_over + away_over) / 2
    avg_under = (home_under + away_under) / 2
    btts_probability = (home_bts + away_bts) / 2
    draw_tendency = (home_draw + away_draw) / 2

    if archetype in ("open_exchange",):
        expected_total_goals = "high"
        btts_probability = min(btts_probability + 0.10, 0.85)
        draw_tendency = max(draw_tendency - 0.05, 0.10)
    elif archetype in ("defensive_grind",):
        expected_total_goals = "low"
        btts_probability = max(btts_probability - 0.15, 0.15)
        draw_tendency = min(draw_tendency + 0.08, 0.45)
    elif archetype == "attack_vs_defense":
        expected_total_goals = "moderate"
        # Attacker usually scores, defender might not → lower BTTS
        btts_probability = max(btts_probability - 0.05, 0.30)
    else:
        expected_total_goals = "moderate"

    # --- Key factors ---
    key_factors: list[str] = []
    if "强压制型" in home_tags:
        key_factors.append(f"主队强压制风格，控球与射门占优")
    if "强压制型" in away_tags:
        key_factors.append(f"客队强压制风格，控球与射门占优")
    if "防守反击型" in home_tags:
        key_factors.append("主队防守反击，定位球与快速转换是关键武器")
    if "防守反击型" in away_tags:
        key_factors.append("客队防守反击，定位球与快速转换是关键武器")
    if "慢热型" in home_tags:
        key_factors.append("主队慢热，前 30 分钟需警惕早早丢球")
    if "慢热型" in away_tags:
        key_factors.append("客队慢热，前 30 分钟需警惕早早丢球")
    if "大比分倾向" in all_tags:
        key_factors.append("双方近期大比分率高，进球数预期偏高")
    if "小比分倾向" in all_tags:
        key_factors.append("双方近期小比分率高，进球数预期偏低")

    # --- Narrative ---
    home_name = "主队"
    away_name = "客队"
    narratives: list[str] = [f"{matchup_label}："]

    if archetype == "open_exchange":
        narratives.append(
            f"两队均以进攻见长，预计将上演一场开放式的进球大战，"
            f"总进球数预期偏高（大2.5球概率约 {avg_over:.0%}）。"
        )
    elif archetype == "defensive_grind":
        narratives.append(
            f"两队均偏重防守，预计比赛节奏缓慢、进球稀少，"
            f"平局风险较高（平局倾向约 {draw_tendency:.0%}）。"
        )
    elif archetype == "attack_vs_defense":
        if home_open:
            narratives.append(
                f"主队进攻强势 vs 客队防守反击，破密集阵是主队的核心课题，"
                f"若久攻不下可能被客队偷袭得手。"
            )
        else:
            narratives.append(
                f"客队进攻强势 vs 主队防守反击，破密集阵是客队的核心课题，"
                f"若久攻不下可能被主队偷袭得手。"
            )
    elif archetype == "slow_start":
        narratives.append(
            f"双方均有慢热特征，前 30 分钟可能互相试探，"
            f"比赛在后段才真正打开。"
        )
    else:
        narratives.append(
            f"两队风格相对均衡，比赛走向取决于临场发挥与战术调整。"
        )

    if btts_probability > 0.55:
        narratives.append(f"双方都有进球能力（BTTS 概率约 {btts_probability:.0%}）。")
    elif btts_probability < 0.35:
        narratives.append(f"某一方可能零封对手（BTTS 概率仅约 {btts_probability:.0%}）。")

    # --- P4-B: context tags ---
    # Short labels that summarize the match's strategic storyline. These are
    # surfaced in the MatchDetailDrawer as quick-scan badges.
    context_tags: list[str] = []
    if archetype == "open_exchange":
        context_tags.append("进球大战预期")
    if archetype == "defensive_grind":
        context_tags.append("平局高危")
    if archetype == "attack_vs_defense":
        context_tags.append("攻防博弈")
    if "慢热型" in all_tags:
        context_tags.append("慢热警告")
    if "防守反击型" in home_tags and "强压制型" in away_tags:
        context_tags.append("反击破压制")
    if "防守反击型" in away_tags and "强压制型" in home_tags:
        context_tags.append("反击破压制")

    # Strength-based tags (requires long_term_strength_score)
    home_str = _metric(home_profile, "long_term_strength_score", 0.5)
    away_str = _metric(away_profile, "long_term_strength_score", 0.5)
    if home_str >= 0.7 and away_str >= 0.7:
        context_tags.append("强强对话")
    elif min(home_str, away_str) < 0.3 and max(home_str, away_str) >= 0.6:
        context_tags.append("黑马挑战")
    elif abs(home_str - away_str) >= 0.3:
        context_tags.append("实力悬殊")

    # Upset potential
    upset_risk = max(
        _metric(home_profile, "upset_potential_score", 0.0),
        _metric(away_profile, "upset_potential_score", 0.0),
    )
    if upset_risk >= 0.6:
        context_tags.append("爆冷风险")

    # --- P4-A: sparks (key storylines / "火花") ---
    # 2-3 short narrative hooks that describe what makes this matchup
    # interesting. These answer the user's "会擦出怎样的火花" question.
    sparks: list[str] = []
    if archetype == "open_exchange":
        sparks.append(
            f"两队进攻火力均突出（大2.5球概率约 {avg_over:.0%}），"
            f"有望上演进球互飙的好戏，中场控制权将是胜负手。"
        )
    elif archetype == "defensive_grind":
        sparks.append(
            f"双方均以防守见长（平局倾向约 {draw_tendency:.0%}），"
            f"比赛可能陷入闷局，定位球或一次失误即可决定胜负。"
        )
    elif archetype == "attack_vs_defense":
        if home_open:
            sparks.append(
                "主队强攻 vs 客队铁桶阵：破密集阵是核心看点，"
                "若主队久攻不下心态急躁，客队反击偷袭可能成为冷门剧本。"
            )
        else:
            sparks.append(
                "客队强攻 vs 主队铁桶阵：破密集阵是核心看点，"
                "若客队久攻不下心态急躁，主队反击偷袭可能成为冷门剧本。"
            )
    if "慢热型" in home_tags and "慢热型" in away_tags:
        sparks.append("双方均有慢热特征，上半场可能沉闷，比赛在下半场才真正打开。")
    elif "慢热型" in home_tags:
        sparks.append("主队慢热，客队若开局抢攻可能早早确立优势。")
    elif "慢热型" in away_tags:
        sparks.append("客队慢热，主队若开局抢攻可能早早确立优势。")

    if upset_risk >= 0.6:
        sparks.append("历史数据显示弱方具备爆冷潜力，本场比赛可能并非一边倒。")
    if home_str >= 0.7 and away_str >= 0.7:
        sparks.append("两支顶级球队正面交锋，胜负取决于细节与临场调整，堪称提前到来的决赛级别较量。")

    # Deduplicate context_tags while preserving order
    seen: set[str] = set()
    context_tags = [t for t in context_tags if not (t in seen or seen.add(t))]

    return {
        "matchup_label": matchup_label,
        "archetype": archetype,
        "pace_label": pace_label,
        "expected_total_goals": expected_total_goals,
        "btts_probability": round(btts_probability, 3),
        "draw_tendency": round(draw_tendency, 3),
        "over_2_5_tendency": round(avg_over, 3),
        "under_2_5_tendency": round(avg_under, 3),
        "key_factors": key_factors[:5],
        "narrative": "".join(narratives),
        "home_tags": sorted(home_tags),
        "away_tags": sorted(away_tags),
        # P4-B: quick-scan context badges
        "context_tags": context_tags[:6],
        # P4-A: 2-3 storyline hooks ("火花")
        "sparks": sparks[:3],
    }
