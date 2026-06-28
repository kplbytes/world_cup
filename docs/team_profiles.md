# 球队画像说明

## 当前目标

球队画像用于解释一支球队的长期实力、近期状态、攻防结构、风格、阵容风险、环境适应和数据可信度。当前代码里，画像已经以受限调整项的形式进入主预测链路，同时保留独立评估端点用于复盘画像效果。

当前约束：

- 会进入 `MatchContext`，但调整幅度受 caps 限制；
- 小组赛和淘汰赛重算链路都应走同一套画像调整逻辑，不能在 knockout 阶段静默关掉画像权重；
- 会影响赛前预测和 AI prompt 输入；
- 赛后仍通过独立评估结果观察画像是“helped / hurt / neutral”；
- 前端比赛详情、球队详情和模型复盘都应读取同一套来源可追溯 payload，其中模型复盘使用 `/api/team-profiles/evaluation`；
- 来源不足时必须显式暴露 `unavailable`、`missing_fields`、`data_quality_score`，不能伪装成真实强信号；
- 仍不做多阶段 T-24 / T-6 / T-90 / T-30 多版本策略文案，评分口径以开赛前最后一份有效预测为准。

## 七个画像模块

### 1. 基础实力画像

回答“这支球队长期强不强”。

主要字段：

- `long_term_strength_score`: 0-100 长期实力分
- `strong_opponent_performance_json`: 对 elite/strong 对手表现
- `middle_opponent_performance_json`: 对 mid 对手表现
- `weak_opponent_performance_json`: 对 weak 对手表现
- `tournament_experience_score`: 大赛经验分

数据来源：本地 Elo、近两年正式比赛结果、世界杯/预选赛样本、对手 tier。
FIFA 排名字段保留为低权重参考；无真实值时标记 missing。

### 2. 近期状态画像

回答“这支球队最近是不是状态好”。

主要字段：

- `recent_form_score`: 0-100 近期状态分
- `profile_modules_json.recent_form.recent_5`
- `profile_modules_json.recent_form.recent_10`
- `profile_modules_json.recent_form.unbeaten_streak`
- `profile_modules_json.recent_form.declining`

计算口径：比赛结果 + 对手强度校正 + 比赛性质权重 + 时间近因权重 + 主客场修正。友谊赛降权，正式比赛高权重。

### 3. 攻防能力画像

回答“这支球队靠什么赢球，容易在哪里出问题”。

主要字段：

- `attack_score`: 0-100 进攻分
- `defense_score`: 0-100 防守分
- `stability_score`: 0-100 稳定性分
- `profile_modules_json.attack_defense.attack_level`
- `profile_modules_json.attack_defense.defense_level`
- `profile_modules_json.attack_defense.tempo_tendency`
- `profile_modules_json.attack_defense.xg`

已有真实数据包括场均进球、场均失球、零封率、低比分/高比分倾向、面对强队失球变化。
StatsBomb xG 只在本地 `data/external/statsbomb/world_cup_xg.json` 覆盖该球队时展示，当前来源限定为 2018/2022 世界杯样本，字段包括 `source`、`competition`、`seasons`、`sample_count`、`xg_for_avg`、`xg_against_avg`。未覆盖球队必须保留 `xg=unavailable` 并加入 `missing_fields_json`。
射门、射正率、定位球、被射门、反击脆弱性、最后阶段丢球等字段当前 unavailable。

### 4. 战术风格画像

回答“这支球队比赛风格是什么”。

主要字段：

- `tactical_style_tags_json`
- `profile_modules_json.tactical_style`

当前为可解释规则近似推断，不接黑盒模型。规则来自进球、失球、平局率、比分分布、低比分/高比分倾向等结构化指标。

可能标签：强压制型、保守低比分型、开放对攻型、小比分倾向、大比分倾向、慢热型、防守反击型、均衡型。

### 5. 阵容与球员画像

回答“这支球队能不能派出真实战斗力”。

主要字段：

- `lineup_integrity_score`
- `injury_risk_score`
- `profile_modules_json.lineup_players`

已接入 FIFA 官方 2026-06-20 Squad List，覆盖 48/48 队 26 人名单，可展示：

- `profile_modules_json.lineup_players.squad_size`
- `profile_modules_json.lineup_players.position_counts`
- `profile_modules_json.lineup_players.average_caps`
- `profile_modules_json.lineup_players.total_caps`
- `profile_modules_json.lineup_players.total_goals`
- `profile_modules_json.lineup_players.top_scorers_in_squad`
- `profile_modules_json.lineup_players.most_capped_players`
- `profile_modules_json.lineup_players.bench_depth`

该来源不是实时伤停、停赛或首发确认 feed，因此 `lineup_integrity_score`、`injury_risk_score`、`captain_status`、`starting_goalkeeper_status`、`yellow_card_suspension_risk`、`confirmed_lineup_level` 仍保持 unavailable，并加入 `missing_fields_json`。系统不会用 mock 或主观判断伪造阵容完整度。

### 6. 比赛环境适应画像

回答“比赛环境对这支球队有没有影响”。

主要字段：

- `rest_days`
- `schedule_fatigue_score`
- `environment_adaptation_score`
- `profile_modules_json.environment`

当前已接入赛程、场地 registry、旅行距离、时差和 Open-Meteo 历史气候基线。场地熟悉度、连续客场、高温/高湿/高海拔专项适应仍没有可靠球队级数据源，因此保持 unavailable 并加入 `missing_fields_json`。

已接入赛程表中的真实 kickoff / venue，用于计算：

- `rest_days`: 上一场到下一场之间的休息天数
- `schedule_fatigue_score`: 基于休息天数的赛程疲劳分
- `profile_modules_json.environment.travel_distance_km`: 上一场场地到下一场场地的大圆距离
- `profile_modules_json.environment.timezone_shift_hours`: 上一场场地到下一场场地的 IANA 时区偏移差
- `environment_adaptation_score`: 基于休息、旅行距离和时差的环境负荷分
- `profile_modules_json.environment.next_match`
- `profile_modules_json.environment.previous_venue`
- `profile_modules_json.environment.next_venue`
- `profile_modules_json.environment.upcoming_venues`
- `profile_modules_json.environment.climate_adaptation`: Open-Meteo 历史气候基线，`is_match_forecast=false`，不是赛时天气预报

场地坐标和时区来自 `data/seed/world-cup-2026-venues.json`，覆盖当前赛程 16 个 venue 名称。天气、气候、场地熟悉度仍 unavailable，因此环境模块仍为 partial。

历史气候基线由 `backend/scripts/build_venue_climate_baseline.py` 生成，来源为 Open-Meteo Historical Weather API，输出到 `data/seed/world-cup-2026-venue-climate.json`。该文件只表示 2015-2024 年 6/7 月历史气候平均，不是 2026 比赛日天气预报；如果快照未生成或 venue 未覆盖，`climate_adaptation` 必须保持 unavailable。

### 7. 数据可信度画像

回答“这个画像能不能信”。

主要字段：

- `data_quality_score`
- `data_quality_json`
- `missing_fields_json`
- `source_list_json`
- `usage_scope`
- `prediction_enabled`

可信度会考虑：

- 是否真实来源
- 是否包含 mock
- 缺失字段数量
- 关键模块 unavailable penalty
- 真实样本量
- 来源列表
- 更新时间
- 是否可复现

`data_quality_json.quality_penalties` 会列出扣分项，例如 `lineup_player_unavailable`、`schedule_environment_unavailable`、`climate_venue_unavailable`、`xg_unavailable`、`shot_volume_unavailable`、`small_competitive_sample`、`contains_mock`。因此阵容、环境、xG/射门等关键字段缺失时，质量分不会被固定下限抬高。

如果包含 `seed_mock_v1`，`data_quality_json.contains_mock=true`，`risk_flags_json` 会包含 `mock_data_present`，并显著降低 `data_quality_score`。API payload 会把核心画像评分和标签暴露为 `null` / 空列表，避免 mock fallback 看起来像真实画像评分。

## 结构化字段

`TeamProfile` 结构化保存：

- `long_term_strength_score`
- `recent_form_score`
- `attack_score`
- `defense_score`
- `stability_score`
- `tournament_experience_score`
- `lineup_integrity_score`
- `injury_risk_score`
- `rest_days`
- `schedule_fatigue_score`
- `environment_adaptation_score`
- `data_quality_score`
- `tactical_style_tags_json`
- `strong_opponent_performance_json`
- `middle_opponent_performance_json`
- `weak_opponent_performance_json`
- `strengths_json`
- `weaknesses_json`
- `risk_flags_json`
- `missing_fields_json`
- `source_list_json`
- `narrative_json`
- `data_quality_json`
- `profile_modules_json`
- `usage_scope`
- `prediction_enabled`

前端 payload 同时提供去掉 `_json` 后缀的便捷字段，如 `strengths`、`risk_flags`、`missing_fields`、`source_list`、`tactical_style_tags`。

## 数据来源

当前主要来源：

- `data/seed/team-profile-match-history.json`: 近两年国际比赛结果快照
- `data/seed/team-profile-world-cup-history.json`: 2014/2018/2022 世界杯结果快照
- `team_ratings`: Elo / FIFA rank 字段。FIFA 排名优先来自官方 FDCP API `https://api.fifa.com/api/v3/rankings?gender=1&count=300`，当前已覆盖 48/48 支球队；CSV 只作为 fallback。
- `TeamProfileMatchHistory`: 入库后的画像比赛历史
- `data/external/statsbomb/world_cup_xg.json`: StatsBomb World Cup xG 本地文件，限定 2018/2022 World Cup
- `data/seed/world-cup-2026-squads.json`: FIFA 官方 Squad List，发布于 2026-06-20，覆盖 48 队 1248 名球员

`source_list_json` 使用可追溯标签，例如 `historical_real:martj42/international_results:<sha12>`、`elo:world_football_elo+fifa_official_ranking:<date>:<effective_date>`、`fifa_ranking:world_football_elo+fifa_official_ranking:<date>:<effective_date>`、`statsbomb_xg:open_data_world_cup:2018_2022`、`fifa_squad:fifa_official_squad_list:2026-06-20`；更完整的 `raw_url`、`raw_sha256`、覆盖日期在 `source_summary_json` / `data_quality_json.rating_source` 中。

外部校验可使用：

- World Football Elo Ratings
- FIFA/Coca-Cola Men's World Ranking 页面更新时间
- FIFA official Squad List PDF
- StatsBomb World Cup xG 本地文件。当前文件只覆盖 2018/2022 世界杯 128 场；48 支球队画像中只有有对应世界杯样本的球队能匹配到 xG，未覆盖球队必须保留 `xg=unavailable`。

## 缺失数据处理

缺少真实数据时必须：

- 字段值写 `null` 或 `"unavailable"`
- 字段名加入 `missing_fields_json`
- 前端展示 unavailable
- 不用 mock 伪装真实数据

## 预测隔离

当前球队画像只通过以下 API 展示：

- `GET /api/team-profiles`
- `GET /api/team-profiles/{team_id}`
- `GET /api/matches/{id}` 中的 `team_profiles`

当前不生成新的 `TeamProfilePrediction`，不进入 Baseline、AI、Ensemble，不改变概率。

## 后续扩展

后续可在独立阶段扩展：

- 赛前画像快照
- Shadow 验证
- Ensemble 接入
- 球员重要性 / 伤停 / 首发数据源
- 旅行、休息、天气、场地适应数据源
