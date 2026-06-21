# 因子准入决策报告

生成时间: 2026-06-19 11:17:34

## 决策统计

- **ACCEPTED_SHADOW**: 18 个因子
- **NEEDS_MORE_DATA**: 10 个因子
- **REJECTED**: 24 个因子

## PROMOTED 因子

*无因子达到 PROMOTED 标准*

## ACCEPTED_SHADOW 因子

| 因子 | 综合得分 | IC | 方向稳定性 |
|------|---------|-----|-----------|
| fifa_rank_diff_factor | 0.850 | 0.472 | 100.0% |
| fifa_points_diff | 0.766 | 0.461 | 100.0% |
| elo_diff | 0.728 | 0.472 | 100.0% |
| defense_strength | 0.637 | 0.329 | 100.0% |
| knockout_experience | 0.628 | 0.402 | 100.0% |
| home_away_neutral_form | 0.625 | 0.335 | 100.0% |
| tournament_experience | 0.605 | 0.399 | 100.0% |
| h2h_last_5 | 0.589 | 0.356 | 100.0% |
| recent_goals_conceded_5 | 0.582 | 0.285 | 100.0% |
| recent_form_10 | 0.572 | 0.335 | 100.0% |
| recent_goal_diff_5 | 0.570 | 0.325 | 100.0% |
| recent_form_5_opp_adjusted | 0.535 | 0.291 | 100.0% |
| attack_strength | 0.522 | 0.280 | 100.0% |
| host_advantage | 0.480 | 0.094 | 100.0% |
| recent_form_5 | 0.466 | 0.290 | 100.0% |
| recent_goals_scored_5 | 0.450 | 0.246 | 100.0% |
| comeback_rate_home | 0.443 | 0.186 | 96.2% |
| scoring_consistency | 0.430 | 0.179 | 100.0% |

## REJECTED 因子

- **inter_confederation_form**: 综合得分=0.008, 效果不足或有害
- **travel_distance_proxy**: 综合得分=0.078, 效果不足或有害
- **elo_fifa_disagreement**: 综合得分=0.084, 效果不足或有害
- **opening_match_effect**: 综合得分=0.091, 效果不足或有害
- **recent_upset_home**: 综合得分=0.098, 效果不足或有害
- **low_scoring_matchup**: 综合得分=0.111, 效果不足或有害
- **dead_rubber**: 综合得分=0.118, 效果不足或有害
- **tournament_stage_pressure**: 综合得分=0.126, 效果不足或有害
- **h2h_draw_rate**: 综合得分=0.128, 效果不足或有害
- **altitude_effect**: 综合得分=0.133, 效果不足或有害
- **defensive_matchup**: 综合得分=0.136, 效果不足或有害
- **form_volatility_away**: 综合得分=0.141, 效果不足或有害
- **must_win_situation**: 综合得分=0.145, 效果不足或有害
- **form_volatility_home**: 综合得分=0.160, 效果不足或有害
- **match_density_30d**: 综合得分=0.187, 效果不足或有害
- **fifa_rank_trend_home**: 综合得分=0.189, 效果不足或有害
- **goal_form_trend_home**: 综合得分=0.206, 效果不足或有害
- **fifa_rank_trend_away**: 综合得分=0.233, 效果不足或有害
- **h2h_avg_goals**: 综合得分=0.235, 效果不足或有害
- **h2h_recency**: 综合得分=0.254, 效果不足或有害
- **draw_tendency_home**: 综合得分=0.260, 效果不足或有害
- **elo_closeness**: 综合得分=0.260, 效果不足或有害
- **match_density_90d**: 综合得分=0.266, 效果不足或有害
- **win_streak_home**: 综合得分=0.286, 效果不足或有害

## 平局预测突破结论

- 最佳平局方案: **approach_1_separate**
  - Draw F1: 40.6%
  - Draw 命中率: 73.6%
  - Brier: 0.5905

## 模型架构结论

- 最佳模型: **standard_lr**
  - Brier: 0.5089
  - 准确率: 60.8%