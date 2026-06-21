# 因子准入决策报告

生成时间: 2026-06-19 11:06:28

## 决策统计

- **ACCEPTED_SHADOW**: 17 个因子
- **NEEDS_MORE_DATA**: 9 个因子
- **REJECTED**: 26 个因子

## PROMOTED 因子

*无因子达到 PROMOTED 标准*

## ACCEPTED_SHADOW 因子

| 因子 | 综合得分 | IC | 方向稳定性 |
|------|---------|-----|-----------|
| fifa_points_diff | 0.914 | 0.461 | 100.0% |
| fifa_rank_diff_factor | 0.862 | 0.472 | 100.0% |
| elo_diff | 0.728 | 0.472 | 100.0% |
| defense_strength | 0.720 | 0.329 | 100.0% |
| home_away_neutral_form | 0.704 | 0.335 | 100.0% |
| knockout_experience | 0.618 | 0.402 | 100.0% |
| recent_goal_diff_5 | 0.603 | 0.325 | 100.0% |
| recent_form_10 | 0.600 | 0.335 | 100.0% |
| tournament_experience | 0.594 | 0.399 | 100.0% |
| h2h_last_5 | 0.586 | 0.356 | 100.0% |
| recent_goals_conceded_5 | 0.585 | 0.285 | 100.0% |
| recent_form_5_opp_adjusted | 0.557 | 0.291 | 100.0% |
| attack_strength | 0.523 | 0.280 | 100.0% |
| recent_form_5 | 0.489 | 0.290 | 100.0% |
| recent_goals_scored_5 | 0.458 | 0.246 | 100.0% |
| comeback_rate_home | 0.431 | 0.186 | 96.2% |
| scoring_consistency | 0.426 | 0.179 | 100.0% |

## REJECTED 因子

- **inter_confederation_form**: 综合得分=0.008, 效果不足或有害
- **travel_distance_proxy**: 综合得分=0.078, 效果不足或有害
- **elo_fifa_disagreement**: 综合得分=0.084, 效果不足或有害
- **opening_match_effect**: 综合得分=0.090, 效果不足或有害
- **recent_upset_home**: 综合得分=0.097, 效果不足或有害
- **h2h_draw_rate**: 综合得分=0.109, 效果不足或有害
- **low_scoring_matchup**: 综合得分=0.111, 效果不足或有害
- **dead_rubber**: 综合得分=0.118, 效果不足或有害
- **tournament_stage_pressure**: 综合得分=0.126, 效果不足或有害
- **defensive_matchup**: 综合得分=0.127, 效果不足或有害
- **altitude_effect**: 综合得分=0.133, 效果不足或有害
- **must_win_situation**: 综合得分=0.135, 效果不足或有害
- **form_volatility_away**: 综合得分=0.142, 效果不足或有害
- **form_volatility_home**: 综合得分=0.158, 效果不足或有害
- **fifa_rank_trend_home**: 综合得分=0.191, 效果不足或有害
- **match_density_30d**: 综合得分=0.194, 效果不足或有害
- **h2h_avg_goals**: 综合得分=0.201, 效果不足或有害
- **goal_form_trend_home**: 综合得分=0.202, 效果不足或有害
- **fifa_rank_trend_away**: 综合得分=0.233, 效果不足或有害
- **elo_closeness**: 综合得分=0.257, 效果不足或有害
- **draw_tendency_home**: 综合得分=0.263, 效果不足或有害
- **h2h_recency**: 综合得分=0.264, 效果不足或有害
- **neutral_draw_rate**: 综合得分=0.269, 效果不足或有害
- **match_density_90d**: 综合得分=0.272, 效果不足或有害
- **draw_tendency_away**: 综合得分=0.285, 效果不足或有害
- **win_streak_home**: 综合得分=0.288, 效果不足或有害

## 平局预测突破结论

- 最佳平局方案: **approach_1_separate**
  - Draw F1: 40.6%
  - Draw 命中率: 73.6%
  - Brier: 1.0284

## 模型架构结论

- 最佳模型: **calibrated_platt**
  - Brier: 0.9780
  - 准确率: 16.4%