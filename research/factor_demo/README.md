# 世界杯历史数据因子研究 Demo

## 概述

本项目是一个与主程序完全隔离的研究 Demo，用于：
1. 构建预测比赛胜、平、负所需的赛前因子
2. 检验每个因子的真实性、稳定性和增量价值
3. 对比现有 Elo + Poisson Baseline
4. 只有通过验收门槛的因子，才允许以 Shadow 模式接入主系统

## 快速开始

```bash
cd research/factor_demo
pip install -r requirements.txt

# 运行完整流水线
python scripts/run_pipeline.py --phase 7

# 只运行到 Phase 3 (Baseline)
python scripts/run_pipeline.py --phase 3

# 运行测试
pytest tests/ -v
```

## 项目结构

```
factor_demo/
├── RESEARCH_PROTOCOL.md     # 研究协议
├── factor_registry.yaml     # 因子注册表
├── config/
│   ├── time_boundaries.yaml # 时间边界
│   └── leak_forbidden_fields.yaml  # 数据泄漏禁止字段
├── data/
│   ├── raw/                 # 原始数据
│   ├── processed/           # 处理后数据
│   └── team_mapping/        # 球队名称映射
├── src/
│   ├── data/                # 数据加载与标准化
│   ├── features/            # 特征工程 (as_of 机制)
│   ├── models/              # 基线模型
│   ├── evaluation/          # 评估指标与回测
│   └── utils/               # 工具函数
├── tests/
│   ├── test_leak_detection.py   # 数据泄漏测试
│   ├── test_features.py         # 因子计算测试
│   └── test_baselines.py        # 基线模型测试
├── scripts/
│   └── run_pipeline.py      # 一键运行流水线
├── outputs/                 # 输出结果
├── requirements.txt
└── README.md
```

## 研究阶段

| 阶段 | 描述 | 状态 |
|------|------|------|
| Phase 0 | 冻结研究协议 | ✅ |
| Phase 1 | 数据采集与标准化 | ✅ |
| Phase 2 | 时间点特征工程 | ✅ |
| Phase 3 | Baseline 建立 | ✅ |
| Phase 4 | 单因子研究 | ✅ |
| Phase 5 | 组合模型与消融实验 | ✅ |
| Phase 6 | 严格历史回放 | ✅ |
| Phase 7 | 因子准入评审 | ✅ |

## 核心原则

- **时间一致性**: 每场比赛只能使用开赛前已公开的数据
- **禁止随机划分**: 必须按时间回放
- **Brier 优先**: Brier Score 为主要指标
- **完整报告**: 研究失败也必须输出结果

## 与主程序隔离

本 Demo 不读取、不修改主程序数据库，所有代码和数据均在 `research/factor_demo/` 目录下。
