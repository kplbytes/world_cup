# Ensemble 融合验证报告

> 验证日期：2026-06-13
> 验证范围：Ensemble 融合引擎权重计算、降级策略、数据隔离
> 验证方法：代码路径分析 + `test_p2plus_hardening.py` 中 `TestEnsemble` 类

---

## 1. 权重示例（全源可用）

**配置来源：** `ai_models.yaml` → `ensemble_defaults`

```yaml
ensemble_defaults:
  system_weight: 0.50
  market_weight: 0.20
  total_ai_weight: 0.30
```

**场景：** 系统预测 + 市场赔率 + AI 预测均可用

```python
# _compute_weights(has_market=True, has_ai=True, ...)
# → sys_w = 0.50, mkt_w = 0.20, total_ai_w = 0.30
```

**AI 内部分配（按 `ensemble_weight`）：**

| 模型 | ensemble_weight | 占 AI 池比例 | 实际权重 |
|------|----------------|-------------|---------|
| DeepSeek Flash | 0.33 | 0.33/(0.33+0.67) = 0.33 | 0.30 × 0.33 = 0.099 |
| DeepSeek Pro | 0.67 | 0.67/(0.33+0.67) = 0.67 | 0.30 × 0.67 = 0.201 |

**最终权重（归一化前）：**

```
system: 0.50
market: 0.20
ai_ai-deepseek-v4-flash-v1: 0.099
ai_ai-deepseek-v4-pro-v1: 0.201
合计: 1.00
```

**最终权重（归一化后）：** 归一化步骤 `weights[key] /= total`，合计 1.0 → 无变化

**融合结果示例：**

```
ensemble_home_win = 0.50 × system_hw + 0.20 × market_hw + 0.099 × flash_hw + 0.201 × pro_hw
ensemble_draw     = 0.50 × system_d  + 0.20 × market_d  + 0.099 × flash_d  + 0.201 × pro_d
ensemble_away_win = 0.50 × system_aw + 0.20 × market_aw + 0.099 × flash_aw + 0.201 × pro_aw
```

---

## 2. 降级示例：无市场数据

**配置：**

```yaml
system_weight_no_market: 0.60
total_ai_weight_no_market: 0.40
```

**场景：** 系统预测 + AI 可用，但无 MarketSnapshot

```python
# _compute_weights(has_market=False, has_ai=True, ...)
# → sys_w = 0.60, mkt_w = 0.0, total_ai_w = 0.40
```

**权重分配：**

| 来源 | 权重 |
|------|------|
| system | 0.60 |
| market | 0.00（排除） |
| AI Flash | 0.40 × 0.33 = 0.132 |
| AI Pro | 0.40 × 0.67 = 0.268 |

**测试验证：** `test_ensemble_degrade_missing_market` — 验证 `weights["system"] > 0.5`

---

## 3. 降级示例：无 AI 数据

**配置：**

```yaml
system_weight_no_ai: 0.80
market_weight_no_ai: 0.20
```

**场景：** 系统预测 + 市场可用，但无 AIPrediction

```python
# _compute_weights(has_market=True, has_ai=False, ...)
# → sys_w = 0.80, mkt_w = 0.20, total_ai_w = 0.0
```

**权重分配：**

| 来源 | 权重 |
|------|------|
| system | 0.80 |
| market | 0.20 |
| AI | 0.00（排除） |

**测试验证：** `test_ensemble_degrade_missing_ai` — 验证 `weights["system"] ≈ 0.80, weights["market"] ≈ 0.20`

---

## 4. 降级示例：系统和市场均不可用

**配置：**

```yaml
system_weight_only: 1.00
```

**场景：** 仅有系统预测，无市场无 AI

```python
# _compute_weights(has_market=False, has_ai=False, ...)
# → sys_w = 1.00, mkt_w = 0.0, total_ai_w = 0.0
```

**权重分配：**

| 来源 | 权重 |
|------|------|
| system | 1.00 |
| market | 0.00 |
| AI | 0.00 |

**结果：** Ensemble 概率 = 系统概率（透传）

**测试验证：** `test_ensemble_system_only` — 验证 `result["home_win"] ≈ 0.5`（与系统输入相同）

---

## 5. AI 缺失示例

**场景：** Flash 和 Pro 均未调用或返回错误

**代码逻辑（`ensemble.py:69-76`）：**

```python
ai_preds = list(session.scalars(
    select(AIPrediction)
    .where(AIPrediction.match_id == match_id)
    .where(AIPrediction.error_code.is_(None))        # 排除错误
    .where(AIPrediction.parsed_home_win.isnot(None))  # 排除未解析
    .where(AIPrediction.real_time_only.is_(False))     # 排除实时
    .order_by(AIPrediction.created_at.desc())
))
```

**当 `ai_preds` 为空时：**
- `has_ai = False`
- 走降级分支（system + market 或 system only）
- AI 权重设为 0

---

## 6. 市场缺失示例

**场景：** 无 `sporttery` 提供商的 MarketSnapshot

**代码逻辑（`ensemble.py:51-66`）：**

```python
market_snap = session.scalar(
    select(MarketSnapshot)
    .where(MarketSnapshot.match_id == match_id)
    .where(MarketSnapshot.provider == "sporttery")
    .order_by(MarketSnapshot.fetched_at.desc())
    .limit(1)
)
market_probs = None  # market_snap 为 None
```

**当 `market_probs is None` 时：**
- `has_market = False`
- 走降级分支（system + AI 或 system only）
- 市场贡献为 0

---

## 7. 单个 AI 调用失败时自动排除

**场景：** Flash 返回 `api_error`，Pro 正常返回

**代码逻辑：**
- `error_code IS NOT NULL` → Flash 被排除在 `ai_preds` 之外
- 仅 Pro 进入 `ai_by_version`
- `num_ai = 1` → AI 总权重按单个模型分配

**权重分配（无市场场景为例）：**

| 来源 | 权重 |
|------|------|
| system | 0.60 |
| AI Pro | 0.40 × 1.0 = 0.40 |

**测试验证：** `test_ensemble_degrade_single_ai_failure` — 验证 `source_probabilities` 中仅含 Pro

---

## 8. Parse Error 不进入 Ensemble

**场景：** AI 返回 `parse_failed`

**代码逻辑：** `AIPrediction.error_code.is_(None)` 过滤条件直接排除

```python
# error_code = "parse_failed" → 不满足 is_(None) → 被过滤
```

**测试验证：** `test_ai_parse_error_not_in_ensemble` — 验证 `source_probabilities` 中 `ai_*` 键数量为 0

---

## 9. Real-Time-Only 不进入 Ensemble

**场景：** 开赛后产生的 AI 预测

**代码逻辑：** `AIPrediction.real_time_only.is_(False)` 过滤条件直接排除

```python
# real_time_only = True → 不满足 is_(False) → 被过滤
```

**测试验证：** `test_ensemble_real_time_only_excluded` — 验证 `source_probabilities` 中 `ai_*` 键数量为 0

---

## 10. Ensemble vs Baseline 评分

**当前状态：无数据**

`evaluation.py` 中 `_evaluate_ensemble` 查询：

```python
ens_pred = session.scalar(
    select(EnsemblePrediction)
    .where(EnsemblePrediction.match_id == match.id)
    .where(EnsemblePrediction.is_pre_match_locked.is_(True))  # 仅 T-30 锁定
    .order_by(EnsemblePrediction.created_at.desc())
    .limit(1)
)
```

当前数据库中：
- `EnsemblePrediction` 行数：0（未在生产环境运行过 `POST /api/ensemble/run`）
- 即使运行过，`is_pre_match_locked` 需要在 T-30 前生成才能参与评分
- `_compute_ai_effect` 需要 `system_results.brier` 和 `ensemble_results.brier` 均非 None 才能比较

**结论：** 无法对比 Ensemble vs Baseline，样本为 0

---

## 11. 当前建议

1. **使用 Baseline（`elo-poisson-v1`）作为主模型** — 这是唯一有评分数据的模型版本
2. **不建议在样本 < 20 场时启用 Ensemble 作为正式预测** — 权重配置未经数据验证
3. **AI 预测作为参考信息展示** — 不参与正式评分，仅展示 AI vs System 的分歧
4. **待样本 ≥ 20 场后**，再运行 `POST /api/ai-evaluation` 评估 AI/Ensemble 的 Brier 差异
5. **考虑权重自适应** — 当前权重为静态配置，未来应根据实际 Brier 表现动态调整
