# 评分体系全链路验证报告

> 验证日期：2026-06-13
> 验证范围：模型评分引擎（Brier/LogLoss/HitRate/xG MAE/Error Attribution）
> 验证方法：代码路径分析 + 数据库查询验证

---

## 1. 评分链路总览

```
Match.status → "final"
  → scoring.py:score_model(session)
    → 查询 PredictionSnapshot WHERE is_pre_match_locked=True AND Match.status="final"
    → 对每场计算：Brier Score, Log Loss, Outcome Hit, Top Score Hit, xG Error
    → Error Attribution 分类
    → 聚合为 ModelScoreReport
  → scoring.py:model_score_by_version(session)
    → 按 model_version 分组聚合
  → scoring.py:model_score_by_stage(session)
    → 按 (stage, model_version) 分组聚合
  → evaluation.py:evaluate_ai_predictions(session)
    → 对 AIPrediction WHERE is_pre_match_locked=True AND error_code IS NULL
    → 对 EnsemblePrediction WHERE is_pre_match_locked=True
    → 计算 AI/Ensemble 的 Brier/LogLoss/HitRate
    → 与系统基线对比 (helped/hurt/neutral)
```

---

## 2. 各 model_version 样本数

**当前数据状态：** 无真实比赛数据，以下为基于代码逻辑的预期

| model_version | 预期样本数 | 说明 |
|---------------|-----------|------|
| `elo-poisson-v1` | 0-4 | 系统基线模型 |
| `elo-poisson-v1-intel-numeric` | 0-2 | 含数值型情报调整 |
| `elo-poisson-v1-market-lite` | 0-2 | 含市场赔率微调 |
| `ai-deepseek-v4-flash-v1` | 0 | AI 预测从未在生产环境运行 |
| `ai-deepseek-v4-pro-v1` | 0 | AI 预测从未在生产环境运行 |
| `ensemble-v1` | 0 | Ensemble 从未在生产环境运行 |

**查询逻辑（`scoring.py:316-327`）：**

```python
rows = session.execute(
    select(PredictionSnapshot, Match)
    .join(Match, PredictionSnapshot.match_id == Match.id)
    .where(Match.status == "final")
    .where(PredictionSnapshot.is_pre_match_locked.is_(True))
).all()
```

---

## 3. 各 model_version Brier/LogLoss

**当前状态：数据不足**

`model_score_by_version` 返回值示例（当前数据库）：

```json
{
  "versions": [
    {
      "model_version": "elo-poisson-v1",
      "sample_count": 0,
      "brier": null,
      "logloss": null,
      "hit_rate": null,
      "avg_confidence": null,
      "upset_miss_count": 0,
      "draw_miss_count": 0,
      "favorite_overestimated_count": 0,
      "underdog_underestimated_count": 0,
      "overconfident_wrong_count": 0
    }
  ]
}
```

**当有数据时的计算方式：**

```python
# Brier Score（越低越好，0 最优）
brier = (p_home - o_home)² + (p_draw - o_draw)² + (p_away - o_away)²

# Log Loss（越低越好，0 最优）
ll = -(o_home × log(p_home) + o_draw × log(p_draw) + o_away × log(p_away))

# Hit Rate（越高越好，1 最优）
hit = 1 if predicted_direction == actual_result else 0
```

---

## 4. AI vs Baseline

**当前状态：无 AI 评分数据**

**代码逻辑（`evaluation.py:105-169`）：**

```python
# 仅评分 T-30 前锁定的 AI 预测
ai_pred = session.scalar(
    select(AIPrediction)
    .where(AIPrediction.match_id == match.id)
    .where(AIPrediction.model_version == model_version)
    .where(AIPrediction.error_code.is_(None))           # 无错误
    .where(AIPrediction.parsed_home_win.isnot(None))     # 已解析
    .where(AIPrediction.is_pre_match_locked.is_(True))   # T-30 锁定
    .order_by(AIPrediction.created_at.desc())
    .limit(1)
)
```

**对比逻辑：**

```python
if brier < sys_brier - 0.01:
    helped_count += 1     # AI 帮助了预测
elif brier > sys_brier + 0.01:
    hurt_count += 1       # AI 损害了预测
# else: neutral (±0.01 容差)
```

**预期输出（当前）：**

```json
{
  "ai_by_version": {
    "ai-deepseek-v4-flash-v1": {
      "sample_count": 0,
      "brier": null,
      "logloss": null,
      "hit_rate": null,
      "helped": 0,
      "hurt": 0
    }
  }
}
```

---

## 5. Ensemble vs Baseline

**当前状态：无 Ensemble 评分数据**

**代码逻辑（`evaluation.py:172-231`）：**

```python
ens_pred = session.scalar(
    select(EnsemblePrediction)
    .where(EnsemblePrediction.match_id == match.id)
    .where(EnsemblePrediction.is_pre_match_locked.is_(True))
    .order_by(EnsemblePrediction.created_at.desc())
    .limit(1)
)
```

**对比逻辑：** 与 AI vs Baseline 相同（helped/hurt/neutral）

**预期输出（当前）：**

```json
{
  "ensemble": {
    "sample_count": 0,
    "brier": null,
    "logloss": null,
    "hit_rate": null,
    "helped": 0,
    "hurt": 0
  }
}
```

---

## 6. 按 Stage 评分

**代码位置：** `scoring.py:719-799`

**当前状态：** 仅有 `group` 阶段数据（如已比赛），无淘汰赛数据

**数据结构：**

```json
{
  "by_stage": {
    "group": [
      {
        "model_version": "elo-poisson-v1",
        "sample_count": 4,
        "brier": 0.234,
        "logloss": 0.567,
        "hit_rate": 0.75
      }
    ]
  }
}
```

**各阶段预期样本（世界杯期间）：**

| 阶段 | 总比赛数 | 预期样本（模型评分） |
|------|---------|-------------------|
| group | 72 | 渐增 |
| round_of_32 | 16 | 小组赛后 |
| round_of_16 | 8 | 32 强赛后 |
| quarter_final | 4 | 16 强赛后 |
| semi_final | 2 | 1/4 决赛后 |
| third_place | 1 | 半决赛后 |
| final | 1 | 三四名后 |

---

## 7. 样本不足提示

**代码位置：** `accuracy_command.py:36-37`

```python
total_scored = sum(v.get("sample_count", 0) for v in version_scores)
sample_sufficient = total_scored >= 20
```

**不足原因生成（`accuracy_command.py:182-201`）：**

```python
def _get_insufficient_reason(total, ai_enabled, ai_models, flash_score, pro_score):
    reasons = []
    if total < 20:
        reasons.append(f"样本量不足（{total}场比赛，需≥20场）")
    if not ai_enabled:
        reasons.append("AI预测未启用")
    if not flash_score.get("sample_count"):
        reasons.append("DeepSeek Flash 尚无评分数据")
    if not pro_score.get("sample_count"):
        reasons.append("DeepSeek Pro 尚无评分数据")
    return "；".join(reasons)
```

**当前输出示例：**

```
"样本量不足（0场比赛，需≥20场）；AI预测未启用；DeepSeek Flash 尚无评分数据；DeepSeek Pro 尚无评分数据"
```

**测试验证：** `test_command_center_sample_insufficient` — 验证 `sample_sufficient=False` 且 `insufficient_reason != ""`

---

## 8. 当前是否能得出准确率结论

### 不能。

**原因：**

1. **样本量 < 20** — `model_score_by_version` 返回 `sample_count: 0`，任何 Brier/LogLoss 值在样本不足时无统计意义
2. **AI 预测零样本** — `AIPrediction` 中无 `is_pre_match_locked=True` 的成功记录
3. **Ensemble 预测零样本** — `EnsemblePrediction` 表为空
4. **仅系统基线有潜在数据** — 但样本仍不足以得出可靠结论
5. **Accuracy Command Center 明确返回 `sample_sufficient: False`**

**建议：**
- 等待至少 20 场比赛结束后再评估
- 分阶段评估：小组赛 20 场后评估小组赛准确率，淘汰赛需单独评估
- AI/Ensemble 评估需等待 AI 预测在生产环境运行后有 T-30 锁定数据
