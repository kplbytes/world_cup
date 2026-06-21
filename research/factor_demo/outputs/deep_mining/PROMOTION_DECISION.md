# 因子准入决策报告

生成时间: 2026-06-19T03:35:41.666806

## 摘要

- **准入 (Promoted)**: 8 个
- **候选 (Candidate)**: 27 个
- **拒绝 (Rejected)**: 17 个
- **数据不足 (Needs More Data)**: 0 个

## 准入因子

### elo_diff
- 覆盖率: 100.0%
- 消融 ΔBrier: 0.0025081628558906166
- IC: 0.46461669458304033
- ICIR: 5.8965141096780425
- 方向稳定性: 1.0
- 原因: 消融正向贡献 (ΔBrier=+0.0025)

### home_away_neutral_form
- 覆盖率: 97.2%
- 消融 ΔBrier: 0.002444570362857723
- IC: 0.33109792857682563
- ICIR: 6.360939157814584
- 方向稳定性: 1.0
- 原因: 消融正向贡献 (ΔBrier=+0.0024)

### host_advantage
- 覆盖率: 100.0%
- 消融 ΔBrier: 0.002444570362857723
- IC: 0.09425081277382358
- ICIR: 2.772683066715666
- 方向稳定性: 1.0
- 原因: 消融正向贡献 (ΔBrier=+0.0024)

### fifa_rank_diff_factor
- 覆盖率: 88.8%
- 消融 ΔBrier: 0.002519300807471536
- IC: -0.46539098569323833
- ICIR: -8.231554103982438
- 方向稳定性: 1.0
- 原因: 消融正向贡献 (ΔBrier=+0.0025)

### fifa_points_diff
- 覆盖率: 88.8%
- 消融 ΔBrier: 0.002519300807471536
- IC: 0.4616284557449444
- ICIR: 7.959190305190972
- 方向稳定性: 1.0
- 原因: 消融正向贡献 (ΔBrier=+0.0025)

### fifa_rank_trend_home
- 覆盖率: 92.8%
- 消融 ΔBrier: 0.002519300807471536
- IC: 0.0044789079076446685
- ICIR: 0.09413366705488818
- 方向稳定性: 0.546
- 原因: 消融正向贡献 (ΔBrier=+0.0025)

### fifa_rank_trend_away
- 覆盖率: 92.2%
- 消融 ΔBrier: 0.002519300807471536
- IC: -0.01411734657863538
- ICIR: -0.3260547949184448
- 方向稳定性: 0.687
- 原因: 消融正向贡献 (ΔBrier=+0.0025)

### elo_fifa_disagreement
- 覆盖率: 88.8%
- 消融 ΔBrier: 0.002519300807471536
- IC: -0.0039222871441431705
- ICIR: -0.1568142184769745
- 方向稳定性: 0.588
- 原因: 消融正向贡献 (ΔBrier=+0.0025)

## 候选因子

- **recent_form_5**: IC显著 (0.2866) 且方向稳定
- **recent_form_10**: IC显著 (0.3330) 且方向稳定
- **recent_form_5_opp_adjusted**: IC显著 (0.2868) 且方向稳定
- **recent_goals_scored_5**: IC显著 (0.2441) 且方向稳定
- **recent_goals_conceded_5**: IC显著 (-0.2814) 且方向稳定
- **recent_goal_diff_5**: IC显著 (0.3220) 且方向稳定
- **attack_strength**: IC显著 (0.2768) 且方向稳定
- **defense_strength**: IC显著 (-0.3251) 且方向稳定
- **official_vs_friendly**: IC显著 (0.0407) 且方向稳定
- **rest_days**: IC显著 (-0.0730) 且方向稳定
- **tournament_experience**: IC显著 (0.3883) 且方向稳定
- **knockout_experience**: IC显著 (0.3925) 且方向稳定
- **h2h_last_5**: IC显著 (0.3452) 且方向稳定
- **draw_tendency_home**: IC显著 (0.0547) 且方向稳定
- **draw_tendency_away**: IC显著 (-0.0678) 且方向稳定
- **draw_tendency_diff**: IC显著 (0.0937) 且方向稳定
- **elo_closeness**: IC显著 (-0.0162) 且方向稳定
- **tournament_draw_rate**: IC显著 (0.0643) 且方向稳定
- **neutral_draw_rate**: IC显著 (-0.0178) 且方向稳定
- **form_volatility_home**: IC显著 (0.0138) 且方向稳定
- **win_streak_away**: IC显著 (-0.1256) 且方向稳定
- **unbeaten_streak_home**: IC显著 (0.1423) 且方向稳定
- **clean_sheet_rate_home**: IC显著 (0.1614) 且方向稳定
- **dead_rubber**: IC显著 (0.0171) 且方向稳定
- **h2h_avg_goals**: IC显著 (0.0195) 且方向稳定
- **h2h_recency**: IC显著 (0.0188) 且方向稳定
- **scoring_consistency**: IC显著 (-0.1798) 且方向稳定

## 拒绝因子

- **match_density_30d**: 贡献不显著或方向不稳定
- **match_density_90d**: 贡献不显著或方向不稳定
- **inter_confederation_form**: 贡献不显著或方向不稳定
- **defensive_matchup**: 贡献不显著或方向不稳定
- **low_scoring_matchup**: 贡献不显著或方向不稳定
- **form_volatility_away**: 贡献不显著或方向不稳定
- **win_streak_home**: 贡献不显著或方向不稳定
- **goal_form_trend_home**: 贡献不显著或方向不稳定
- **comeback_rate_home**: 贡献不显著或方向不稳定
- **tournament_stage_pressure**: 贡献不显著或方向不稳定
- **opening_match_effect**: 贡献不显著或方向不稳定
- **must_win_situation**: 贡献不显著或方向不稳定
- **altitude_effect**: 贡献不显著或方向不稳定
- **travel_distance_proxy**: 贡献不显著或方向不稳定
- **h2h_draw_rate**: 贡献不显著或方向不稳定
- **recent_upset_home**: 贡献不显著或方向不稳定
- **goal_difference_momentum**: 贡献不显著或方向不稳定

## 平局专项因子评估

- **draw_tendency_home**: 决策=candidate, 平局相关性=0.04217371951396487, 原因=IC显著 (0.0547) 且方向稳定
- **draw_tendency_away**: 决策=candidate, 平局相关性=0.05817701782606805, 原因=IC显著 (-0.0678) 且方向稳定
- **draw_tendency_diff**: 决策=candidate, 平局相关性=-0.011918375806774309, 原因=IC显著 (0.0937) 且方向稳定
- **elo_closeness**: 决策=candidate, 平局相关性=0.11781290673388453, 原因=IC显著 (-0.0162) 且方向稳定
- **defensive_matchup**: 决策=rejected, 平局相关性=0.03648886573076825, 原因=贡献不显著或方向不稳定
- **tournament_draw_rate**: 决策=candidate, 平局相关性=0.0546414267827407, 原因=IC显著 (0.0643) 且方向稳定
- **neutral_draw_rate**: 决策=candidate, 平局相关性=0.06361477536419403, 原因=IC显著 (-0.0178) 且方向稳定
- **low_scoring_matchup**: 决策=rejected, 平局相关性=0.007430524230246484, 原因=贡献不显著或方向不稳定
- **h2h_draw_rate**: 决策=rejected, 平局相关性=0.06372263515769089, 原因=贡献不显著或方向不稳定
