# 世界杯历史数据因子研究 Demo

## 概述

本项目是一个与主程序完全隔离的研究 Demo，用于：
1. 构建预测比赛胜、平、负所需的赛前因子
2. 检验每个因子的真实性、稳定性和增量价值
3. 对比现有 Elo + Poisson Baseline
4. 只有通过验收门槛的因子，才允许以 Shadow 模式接入主系统

## What's New in v5.0

- **统一研究流水线** (`scripts/run_full_research.py`)：一键运行 11 个 Phase，从数据加载到因子准入决策
- **共享工具模块** (`scripts/pipeline_utils.py`)：提取各轮验证脚本的公共代码，消除重复
- **新增模型**：`RegularizedLogisticBaseline`、`LightGBMBaseline`、`AblationModel`、`CalibratedModel`
- **增强评估指标**：Brier Skill Score、多分类 ECE、可靠性曲线、因子方向稳定性
- **概率校准模块** (`src/evaluation/calibration.py`)：Platt 缩放、等距回归校准、校准曲线
- **分层评估模块** (`src/evaluation/stratification.py`)：按年份/赛事/场地/强弱/场景等多维度分层
- **场景分析**：世界杯淘汰赛、跨洲对抗、强弱悬殊、实力接近等专项场景评估
- **FIFA 排名数据加载** (`src/data/loader.py`)：支持 FIFA 排名数据注入与 as-of 匹配
- **赔率数据加载** (`src/data/loader.py`)：支持赔率数据注入与隐含概率计算
- **跨年份/跨赛事稳定性分析**：通过变异系数评估模型稳定性

## 快速开始

```bash
# 完整研究流水线
cd research/factor_demo
pip install -r requirements.txt
python -m scripts.run_full_research

# 带参数运行
python -m scripts.run_full_research --output-dir outputs/my_run --sample-size 5000

# 运行测试
pytest tests/ -v
```

### 其他运行方式

```bash
# 旧版流水线（兼容）
python scripts/run_pipeline.py --phase 7

# 只运行到 Phase 3 (Baseline)
python scripts/run_pipeline.py --phase 3
```

## 项目结构

```
factor_demo/
├── RESEARCH_PROTOCOL.md          # 研究协议
├── factor_registry.yaml          # 因子注册表（21个因子）
├── config/
│   ├── time_boundaries.yaml      # 时间边界定义
│   └── leak_forbidden_fields.yaml # 数据泄漏禁止字段
├── data/
│   ├── raw/                      # 原始数据
│   ├── processed/                # 处理后数据
│   └── team_mapping/             # 球队名称映射
├── src/
│   ├── data/
│   │   └── loader.py             # 数据加载（比赛结果 + FIFA排名 + 赔率）
│   ├── features/
│   │   ├── as_of.py              # 时间点特征工程 (as_of 机制)
│   │   └── calculator.py         # 因子计算函数（21个因子）
│   ├── models/
│   │   └── baseline.py           # 基线模型（HomeFixed/Frequency/EloLogistic/
│   │                              #  EloPoisson/MarketImplied/RegularizedLogistic/
│   │                              #  LightGBM/Ablation/Calibrated）
│   ├── evaluation/
│   │   ├── metrics.py            # 评估指标（Brier/ECE/BSS/可靠性曲线/方向稳定性）
│   │   ├── calibration.py        # 概率校准（Platt/Isotonic/校准曲线）
│   │   ├── stratification.py     # 分层评估（年份/赛事/场地/强弱/场景）
│   │   ├── backtest.py           # 历史回测（滚动/Walk-Forward/消融/世界杯回测）
│   │   └── bootstrap.py          # Bootstrap 置信区间
│   └── utils/
│       ├── elo_replay.py         # Elo 回放
│       └── time_utils.py         # 时间工具
├── tests/
│   ├── test_leak_detection.py    # 数据泄漏测试
│   ├── test_features.py          # 因子计算测试
│   ├── test_baselines.py         # 基线模型测试
│   └── test_round4_validation.py # 第四轮验证测试
├── scripts/
│   ├── run_full_research.py      # 统一研究流水线（11 Phase）
│   ├── pipeline_utils.py         # 共享流水线工具
│   ├── run_pipeline.py           # 旧版流水线
│   ├── run_round2.py             # 第二轮验证
│   ├── run_round3.py             # 第三轮验证
│   ├── run_round4.py             # 第四轮验证
│   └── regenerate_decision.py    # 重新生成准入决策
├── outputs/                      # 输出结果
├── requirements.txt
└── README.md
```

## 数据来源

| 数据 | 说明 | 必需性 |
|------|------|--------|
| International Football Results | Kaggle 国际足球历史比赛结果 (1872-至今) | 必需 |
| FIFA Rankings (`fifa_ranking.csv`) | FIFA 世界排名数据 | 可选 |
| Odds Data (`odds_data.csv`) | 赔率数据（主胜/平/客胜） | 可选 |

- **主数据源**: [Kaggle International Football Results](https://www.kaggle.com/datasets/martj42/international-football-results-from-1872-to-2017)
- **FIFA 排名**: [Kaggle FIFA World Ranking](https://www.kaggle.com/datasets/cashncarry/fifaworldranking)
- **赔率数据**: 需自行准备，格式为 `match_date, home_team, away_team, odds_home, odds_draw, odds_away`

## 研究阶段

| 阶段 | 描述 | 状态 |
|------|------|------|
| Phase 0 | 冻结研究协议 | ✅ |
| Phase 1 | 数据采集与标准化 | ✅ |
| Phase 2 | 时间点特征工程 | ✅ |
| Phase 3 | Baseline 建立（5个模型） | ✅ |
| Phase 4 | 单因子研究 | ✅ |
| Phase 5 | 组合模型与消融实验 | ✅ |
| Phase 6 | Walk-Forward 验证 | ✅ |
| Phase 7 | 严格时间递进世界杯回测 | ✅ |
| Phase 8 | 概率校准 | ✅ |
| Phase 9 | 分层评估 | ✅ |
| Phase 10 | 因子准入决策 | ✅ |
| Phase 11 | 生成输出产物摘要 | ✅ |

## 因子实现状态

| # | 因子名 | 分组 | 状态 | 说明 |
|---|--------|------|------|------|
| 1 | elo_diff | rating | ✅ 已实现 | 赛前Elo差值 |
| 2 | fifa_rank_diff | rating | ⏸ 跳过 | FIFA排名差值（覆盖率不足） |
| 3 | recent_form_5 | form | ✅ 已实现 | 最近5场状态 |
| 4 | recent_form_10 | form | ✅ 已实现 | 最近10场状态 |
| 5 | recent_form_5_opp_adjusted | form | ✅ 已实现 | 对手强度修正后的近期状态 |
| 6 | recent_goals_scored_5 | attack_defense | ✅ 已实现 | 近5场进球数 |
| 7 | recent_goals_conceded_5 | attack_defense | ✅ 已实现 | 近5场失球数 |
| 8 | recent_goal_diff_5 | attack_defense | ✅ 已实现 | 近5场净胜球 |
| 9 | attack_strength | attack_defense | ✅ 已实现 | 进攻强度 |
| 10 | defense_strength | attack_defense | ✅ 已实现 | 防守强度 |
| 11 | official_vs_friendly | form | ✅ 已实现 | 正式比赛与友谊赛分层表现 |
| 12 | home_away_neutral_form | venue | ✅ 已实现 | 主场/客场/中立场表现 |
| 13 | rest_days | fatigue | ✅ 已实现 | 休息天数 |
| 14 | match_density_30d | fatigue | ✅ 已实现 | 30天比赛密度 |
| 15 | match_density_90d | fatigue | ✅ 已实现 | 90天比赛密度 |
| 16 | tournament_experience | experience | ✅ 已实现 | 大赛经验 |
| 17 | knockout_experience | experience | ✅ 已实现 | 淘汰赛经验 |
| 18 | inter_confederation_form | confederation | ✅ 已实现 | 同洲/跨洲表现 |
| 19 | host_advantage | venue | ✅ 已实现 | 东道主/半主场效应 |
| 20 | h2h_last_5 | h2h | ✅ 已实现 | 历史交锋(近5场) |
| 21 | odds_implied_prob | market | ⏸ 跳过 | 赔率隐含概率（覆盖率不足） |
| 22 | odds_movement | market | ⏸ 跳过 | 赔率变化（覆盖率不足） |

> 注：共 21 个因子注册，其中 18 个已实现并参与模型训练，3 个因数据覆盖率不足暂跳过。

## 核心原则

- **时间一致性**: 每场比赛只能使用开赛前已公开的数据
- **禁止随机划分**: 必须按时间回放
- **Brier 优先**: Brier Score 为主要指标
- **完整报告**: 研究失败也必须输出结果
- **数据泄漏防护**: `config/leak_forbidden_fields.yaml` 明确禁止使用赛后数据
- **概率校准**: 模型输出必须经过校准验证

## 与主程序隔离

本 Demo 不读取、不修改主程序数据库，所有代码和数据均在 `research/factor_demo/` 目录下。
