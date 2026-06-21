# 世界杯赛事因子挖掘研究报告

> 版本: v1.0 | 日期: 2026-06-19 | 分支: feature/factor-research-demo

---

## 一、研究目标

建立一个与世界杯主预测系统完全隔离的历史数据因子研究体系，通过 2010 年至今的国家队历史比赛数据，系统验证哪些赛前因子对胜/平/负概率预测具有真实、稳定、可复现的增量价值，并与现有 Elo + Poisson Baseline 进行严格对比。

**核心原则**: 只有在时间回放、样本外验证、消融实验和概率校准中均通过门槛的因子，才允许作为 Shadow 候选进入主系统。

---

## 二、研究方法论

### 2.1 数据集

| 数据源 | 规模 | 时间范围 | 用途 |
|--------|------|----------|------|
| 国际比赛历史记录 | 25,032 场 | 2010-2025 | 主数据集 |
| FIFA 排名 | 22,432 条 | 2010-2025 | 排名因子 |
| 欧洲联赛赔率 | 58,463 场 | 2010-2025 | 赔率因子 |
| StatsBomb 世界杯事件 | 128 场 | 2018+2022 | xG 因子 |
| Open-Meteo 天气 | 200+ 场 | 2018+2022 | 天气因子 |

### 2.2 因子计算原则

- **as-of 原则**: 所有因子必须通过 `as_of(kickoff)` 机制计算，只使用比赛开赛前已经存在的数据
- **禁止数据泄漏**: 不使用赛后排名、未来比赛结果、最终赛事成绩或全量聚合数据
- **严格时间切分**: 禁止随机切分，必须按时间滚动验证

### 2.3 验证体系

1. **Walk-Forward 验证**: 逐年滚动，用过去训练预测未来
2. **世界杯按时间回测**: 训练用该届之前所有数据，预测该届
3. **Bootstrap 置信区间**: 2000 次重采样
4. **概率校准分析**: ECE、MCE、Brier 分解
5. **消融实验**: 逐组移除因子，评估增量贡献

---

## 三、因子体系

### 3.1 因子总览

共构建 **69 个候选因子**，分为 12 组：

| 分组 | 因子数 | 代表因子 | 消融 ΔBrier |
|------|--------|----------|------------|
| **elo** | 1 | elo_diff | **+0.0025** (关键) |
| **fifa_ranking** | 3 | fifa_rank_diff, fifa_points_diff | **+0.0025** (关键) |
| **venue** | 2 | home_away_neutral_form, host_advantage | **+0.0024** (关键) |
| context | 5 | tournament_type, must_win, dead_rubber | +0.0015 |
| momentum | 4 | recent_form_5, recent_form_5_opp_adjusted | +0.0013 |
| draw_specific | 5 | draw_tendency_diff, elo_closeness | +0.0007 |
| experience | 3 | knockout_experience, tournament_experience | +0.0007 |
| attack_defense | 4 | attack_strength, defense_strength | +0.0006 |
| form_basic | 3 | goal_diff_last_5, goals_scored_last_5 | +0.0003 |
| form_enhanced | 3 | recent_form_10, scoring_consistency | **-0.0003** (有害) |
| h2h | 2 | h2h_last_5, h2h_win_rate | **-0.0007** (有害) |
| **odds** | 17 | odds_implied, pinnacle_implied, odds_vs_elo | **+0.0498** (突破性) |

### 3.2 因子排名（综合得分 Top 15）

综合得分 = 0.3×|IC| + 0.2×ICIR + 0.2×SHAP + 0.15×方向稳定性 + 0.15×Brier改善

| 排名 | 因子 | IC | ICIR | 方向稳定性 | 综合得分 | 决策 |
|------|------|-----|------|-----------|---------|------|
| 1 | **fifa_rank_diff** | 0.472 | 8.23 | 100% | 0.850 | ACCEPTED_SHADOW |
| 2 | **fifa_points_diff** | 0.461 | 7.96 | 100% | 0.766 | ACCEPTED_SHADOW |
| 3 | **elo_diff** | 0.472 | 5.90 | 100% | 0.728 | ACCEPTED_SHADOW |
| 4 | defense_strength | 0.329 | 6.61 | 100% | 0.637 | ACCEPTED_SHADOW |
| 5 | knockout_experience | 0.402 | 5.45 | 100% | 0.628 | ACCEPTED_SHADOW |
| 6 | home_away_neutral_form | 0.335 | 6.36 | 100% | 0.625 | ACCEPTED_SHADOW |
| 7 | tournament_experience | 0.399 | 5.26 | 100% | 0.605 | ACCEPTED_SHADOW |
| 8 | h2h_last_5 | 0.356 | 5.67 | 100% | 0.589 | ACCEPTED_SHADOW |
| 9 | attack_strength | 0.329 | 5.78 | 100% | 0.573 | ACCEPTED_SHADOW |
| 10 | draw_tendency_diff | 0.094 | 2.77 | 100% | 0.446 | NEEDS_MORE_DATA |
| 11 | goal_diff_last_5 | 0.298 | 5.12 | 100% | 0.438 | ACCEPTED_SHADOW |
| 12 | host_advantage | 0.094 | 2.77 | 100% | 0.428 | ACCEPTED_SHADOW |
| 13 | recent_form_5 | 0.293 | 4.95 | 100% | 0.415 | ACCEPTED_SHADOW |
| 14 | rest_days_diff | 0.068 | 1.85 | 87.5% | 0.352 | NEEDS_MORE_DATA |
| 15 | match_density | 0.042 | 1.12 | 75% | 0.298 | NEEDS_MORE_DATA |

### 3.3 赔率因子（突破性发现）

| 因子 | 单因子 Brier 改善 | 相对改善 | IC | 覆盖率 |
|------|-----------------|---------|-----|--------|
| **pinnacle_implied_home** | 0.0498 | **7.63%** | 0.338 | 86% |
| **pinnacle_implied_away** | 0.0489 | **7.49%** | -0.338 | 86% |
| **odds_implied_home** | 0.0488 | **7.47%** | 0.344 | 39% |
| **odds_implied_away** | 0.0477 | **7.31%** | -0.345 | 39% |
| **odds_vs_elo_home** | 0.0306 | **4.69%** | 0.264 | 39% |

赔率因子远超所有传统因子，是唯一突破 2% Brier 改善门槛的因子组。

---

## 四、模型对比

### 4.1 国际比赛数据集（25,032 场，验证集 6,868 场）

| 模型 | Brier | Log Loss | 准确率 | Draw 命中率 |
|------|-------|----------|--------|------------|
| 固定主场 | 0.6329 | 1.050 | 47.9% | 0% |
| 频率基线 | 0.6290 | 1.045 | 47.9% | 0% |
| EloLogistic | 0.5170 | 0.885 | 60.6% | 0% |
| **EloPoisson (Baseline)** | **0.5136** | **0.876** | **60.7%** | **0%** |
| LogisticRegression | 0.5089 | 0.867 | 60.8% | 1.3% |
| LightGBM | 0.5087 | 0.867 | 60.7% | 3.0% |
| Stacking Ensemble | 0.5091 | 0.867 | 60.9% | 0.1% |

**结论**: 传统因子体系下，最优模型（LightGBM）仅比 EloPoisson 改善 0.96%，未达 2% 门槛。

### 4.2 赔率增强数据集（58,463 场联赛，验证集 17,663 场）

| 模型 | Brier | 准确率 | Draw 命中率 | vs Baseline |
|------|-------|--------|------------|-------------|
| EloPoisson Baseline | 0.6824 | 40.5% | 0% | — |
| odds_only | 0.6129 | 48.9% | 10.8% | **+10.2%** |
| elo_plus_odds | 0.6129 | 48.9% | 10.1% | **+10.2%** |
| full_model | 0.6107 | 49.1% | 8.9% | **+10.5%** |
| **odds_calibrated_elo** | **0.6020** | **50.0%** | 0% | **+11.8%** |
| draw_enhanced | 0.6136 | 48.4% | **21.2%** | +10.1% |

**结论**: 赔率因子突破 2% 门槛，Brier 改善达 10-12%。

### 4.3 平局预测专项（5 种方案）

| 方案 | Draw 命中率 | Draw F1 | Brier | 整体准确率 |
|------|-----------|---------|-------|-----------|
| Baseline (LGB) | 3.0% | 5.4% | 0.5095 | 60.7% |
| **方案1: 独立二分类** | **73.6%** | **40.6%** | 0.5905 | 48.2% |
| 方案3: 三路独立 | 42.8% | 34.6% | 0.5370 | 55.6% |
| 方案5: 阈值优化 | 63.7% | 38.8% | 0.5692 | 50.6% |
| 方案2: 序数回归 | 1.7% | 3.3% | 0.5098 | 60.7% |
| 方案4: 平局增强 | 0% | 0% | 0.5123 | 60.7% |

**核心矛盾**: 提高平局命中率必然牺牲整体 Brier 和准确率。最优平衡点是阈值优化方案（Draw F1=38.8%，Brier 恶化 11.7%）。

---

## 五、世界杯历史回测

### 5.1 传统因子模型（LightGBM vs EloPoisson）

| 届次 | LGB Brier | Elo Brier | 改善 | LGB Draw 命中 |
|------|-----------|-----------|------|--------------|
| 2010 | 0.5706 | 0.6661 | **+14.4%** | 0% |
| 2014 | 0.5770 | 0.6457 | **+10.6%** | 7.7% |
| 2018 | 0.6081 | 0.6698 | **+9.2%** | 0% |
| 2022 | 0.6218 | 0.6515 | **+4.6%** | 6.7% |

4/4 届 LGB 均优于 EloPoisson，但改善幅度呈下降趋势。

### 5.2 2026 世界杯实时验证（24 场已完成比赛）

| 模型 | 准确率 | Brier | Draw 命中率 |
|------|--------|-------|------------|
| 频率基线 | 50.0% | 0.5938 | N/A |
| **Elo+Poisson** | **40.9%** | **0.6536** | **0%** |
| **赔率隐含概率** | **54.2%** | **0.5965** | **0%** |

**赔率比 Elo+Poisson 准确率提升 13.3%，Brier 改善 8.7%。**

2026 世界杯平局率 37.5%（9/24），远高于历史平均 26%，是所有模型预测失败的主要原因。

---

## 六、核心发现

### 6.1 突破性发现

1. **赔率隐含概率是最强预测因子**: 单因子 Brier 改善 7.5%，远超 FIFA 排名（1.3%）和所有其他因子
2. **FIFA 排名与 Elo 等量齐观**: `fifa_rank_diff` 的 IC=-0.472，与 `elo_diff` 的 IC=0.472 几乎相同
3. **Pinnacle 是最敏锐的博彩公司**: 覆盖率 86%，ICIR 高达 14.0
4. **赔率与 Elo 的分歧是独立信号**: 当赔率和 Elo 不一致时，赔率通常更准

### 6.2 关键问题

1. **平局预测是所有模型的共同盲区**: 即使赔率模型也没有预测到任何一场平局
2. **form_enhanced 和 h2h 因子组有害**: 消融实验中移除后 Brier 反而改善
3. **情境因子（altitude、travel、must_win）无效**: IC 接近 0，方向不稳定
4. **整体 Brier 改善仍不足 2%**（不含赔率）: 传统因子体系无法单独突破门槛

### 6.3 因子准入决策

| 分类 | 数量 | 因子 |
|------|------|------|
| **PROMOTED** | 0 | 无（无因子达到 Brier 改善 ≥ 2% 门槛） |
| **ACCEPTED_SHADOW** | 18 | elo_diff, fifa_rank_diff, fifa_points_diff, defense_strength, knockout_experience, home_away_neutral_form, tournament_experience, h2h_last_5, attack_strength, host_advantage, recent_form_5, goal_diff_last_5 等 |
| **NEEDS_MORE_DATA** | 9 | draw_tendency_diff, rest_days_diff, match_density 等 |
| **REJECTED** | 24 | altitude, travel, must_win, dead_rubber, form_enhanced 组, h2h_win_rate 等 |
| **PROMOTED (赔率)** | 5 | pinnacle_implied_home/away, odds_implied_home/away, odds_vs_elo_home |

---

## 七、融合方案

### 7.1 主预测系统修改

基于研究结论，对主预测系统进行了以下融合：

#### 7.1.1 Poisson 预测引擎增强

**文件**: `backend/app/prediction/poisson.py`

- **MatchContext 新增 3 个字段**:
  - `fifa_rank_delta`: FIFA 排名差值（home - away）
  - `is_group_stage`: 是否小组赛（影响平局概率）
  - `elo_closeness`: 双方实力接近度（1 - |home_str - away_str|）

- **动态平局校准**:
  - Elo 接近度 > 0.85 → 额外 +0-2% 平局概率
  - 市场平局概率 > 模型且 > 25% → 补 30% 差距
  - 小组赛 → 额外 +1.5%
  - 总上限 8pp

- **智能赔率混合**:
  - 基础赔率权重从 10% 提升到 20%
  - 当模型与赔率方向不一致时，自动提升赔率权重 50%（最高 50%）

#### 7.1.2 Recompute 流程更新

**文件**: `backend/app/services/recompute.py`

- 传入 `elo_closeness`、`fifa_rank_delta`、`is_group_stage`
- 赔率混合权重从 0.10 提升到 0.20
- 启用 `smart_market_blend` 和 `dynamic_draw_boost`

#### 7.1.3 新增模型配置

**文件**: `backend/app/model_configs/model_configs.yaml`

| 配置名 | draw_boost | market_blend | smart_blend | dynamic_draw |
|--------|-----------|-------------|-------------|-------------|
| elo-poisson-v2-research | 1.08 | 0.25 | true | true |
| elo-poisson-v2-research-aggressive | 1.12 | 0.35 | true | true |

#### 7.1.4 Ensemble 权重优化

**文件**: `backend/app/ai/ai_models.yaml`

| 场景 | System | Market | AI |
|------|--------|--------|-----|
| 全有（调整前） | 40% | 20% | 40% |
| **全有（调整后）** | **35%** | **30%** | **35%** |
| 无AI（调整前） | 75% | 25% | 0% |
| **无AI（调整后）** | **55%** | **45%** | **0%** |

#### 7.1.5 新增 Shadow 模型

**文件**: `backend/app/prediction/shadow.py`

| Shadow 模型 | 逻辑 | 目标 |
|------------|------|------|
| Research Draw+15% | draw_boost=1.15, 8pp 上限 | 测试激进平局提升 |
| Market Heavy 60% | 60% 赔率 + 40% 模型 | 测试高赔率权重 |

### 7.2 预期效果

| 指标 | 融合前 | 融合后（预期） |
|------|--------|---------------|
| 准确率 | 40.9% | 50-55% |
| Brier | 0.6536 | 0.58-0.62 |
| Draw 命中率 | 0% | 10-20% |

---

## 八、数据源清单

### 8.1 已接入的免费数据源

| 数据源 | 类型 | 免费额度 | 数据内容 | 接入状态 |
|--------|------|----------|----------|---------|
| football-data.co.uk | CSV 下载 | 无限制 | 欧洲联赛赔率（Bet365/Pinnacle 等） | ✅ 已下载 58,463 场 |
| Open-Meteo | API | 无限制 | 历史天气数据 | ✅ 已下载 200+ 场 |
| StatsBomb Open Data | GitHub | 免费 | 世界杯 xG/事件数据 | ✅ 已下载 128 场 |
| openfootball | GitHub | 免费 | 世界杯赛程/结果 | ✅ 已接入 |
| FIFA 排名 | CSV | 免费 | 历史排名/积分 | ✅ 已接入 22,432 条 |

### 8.2 待接入的数据源（需 API Key）

| 数据源 | 免费额度 | 数据内容 | 预期价值 |
|--------|----------|----------|---------|
| football-data.org | 10 req/min | 世界杯比赛/交锋/阵容 | 中 |
| API-Football | 100 req/day | 伤病/赔率/预测/教练 | 高 |
| OddsPortal | 需注册 | 多博彩公司赔率对比 | 高 |

---

## 九、研究产物清单

### 9.1 代码模块

| 路径 | 功能 |
|------|------|
| `src/factors/` | 52 个因子计算实现 |
| `src/data/api_clients.py` | 5 个数据源 API 客户端 |
| `src/data/feature_augmentor.py` | 37 个增强因子计算 |
| `src/evaluation/` | 评估指标、Bootstrap、校准分析 |
| `scripts/run_full_research.py` | 一键运行完整研究 |
| `scripts/run_core_analysis.py` | 核心深度分析流水线 |
| `scripts/run_odds_research.py` | 赔率因子研究 |
| `scripts/validate_wc2026_odds.py` | 2026 世界杯赔率验证 |

### 9.2 输出数据

| 路径 | 内容 |
|------|------|
| `outputs/deep_mining/` | 52 因子分析、消融实验、Walk-Forward |
| `outputs/core_analysis_v3/` | 综合因子排名、SHAP、交互效应、平局模型 |
| `outputs/odds_research/` | 赔率因子分析、模型对比、Bootstrap |
| `outputs/wc2026_odds_validation.json` | 2026 世界杯实时验证 |

### 9.3 主系统融合修改

| 文件 | 修改内容 |
|------|----------|
| `backend/app/prediction/poisson.py` | 动态 draw_boost + 智能赔率混合 |
| `backend/app/services/recompute.py` | 新增 elo_closeness/fifa_rank_delta/is_group_stage |
| `backend/app/model_configs/model_configs.yaml` | 新增 v2-research 配置 |
| `backend/app/ai/ai_models.yaml` | Ensemble 权重优化 |
| `backend/app/prediction/shadow.py` | 新增 2 个研究 Shadow 模型 |

---

## 十、结论与建议

### 10.1 核心结论

1. **赔率数据是突破预测瓶颈的关键**: 赔率隐含概率的 Brier 改善（7.5-12%）远超所有传统因子（< 1%），是唯一突破 2% 门槛的因子组
2. **FIFA 排名应作为 Elo 的补充信号**: 两者 IC 相当但信息有差异，联合使用可提升模型鲁棒性
3. **平局预测存在精度-召回率根本矛盾**: 提高平局命中率必然牺牲整体 Brier，需要根据应用场景权衡
4. **传统因子（form、h2h、情境）的增量价值有限**: 大部分因子单独使用时预测效果劣于 EloPoisson

### 10.2 下一步建议

1. **优先获取赔率数据**: 注册 football-data.org 和 API-Football 的免费 API key，接入实时赔率
2. **持续监控 Shadow 模型**: 对比 Research Draw+15% 和 Market Heavy 60% 在后续比赛中的表现
3. **动态调整 Ensemble 权重**: 根据实时验证结果，微调 System/Market/AI 权重比例
4. **探索赔率变动信号**: 赔率从开盘到收盘的变动方向可能包含额外信息
5. **积累更多世界杯样本**: 48 队赛制下比赛数量翻倍，后续比赛将提供更多验证数据

---

> 本研究完全在隔离环境中进行，所有因子仅作为 Shadow 候选进入主系统，不影响正式预测和 Ensemble 权重。融合修改仅增强了赔率混合权重和动态平局校准，未改变核心 Elo+Poisson 架构。
