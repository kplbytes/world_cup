# AI 调用链路验证报告

> 验证日期：2026-06-13
> 验证范围：AI 预测从 API 调用到入库的完整链路
> 验证方法：代码路径分析 + 单元测试覆盖

---

## 1. Flash 模型调用示例

```
POST /api/ai-predictions/run?match_id=WC2026_A1_BRA_V_CRC&model_version=ai-deepseek-v4-flash-v1
```

**调用链路：**

```
ai_routes.py:ai_predictions_run()
  → service.py:run_ai_prediction(session, match_id, "ai-deepseek-v4-flash-v1")
    → model_registry.py:get_model_config("ai-deepseek-v4-flash-v1")
      → 返回 AIModelConfig(model_id="deepseek-v4-flash", cost_tier="low", latency_tier="fast")
    → service.py:_build_prediction_request(session, match_id)
      → 从 PredictionSnapshot 获取系统预测
      → 从 MarketSnapshot 获取市场赔率
      → 从 MatchIntelligence 获取情报
    → prompt_builder.py:build_prediction_prompt(request, "worldcup-ai-v1")
    → service.py:_get_provider(provider_config)
      → 实例化 DeepSeekProvider(provider_config)
    → deepseek.py:DeepSeekProvider.predict(model_config, prompt)
      → HTTP POST https://api.deepseek.com/chat/completions
      → model: "deepseek-v4-flash", temperature: 0, response_format: json_object
    → parser.py:parse_ai_response(raw_response)
    → 入库 AIPrediction
```

**成功返回示例：**

```json
{
  "status": "success",
  "prediction_id": 42,
  "model_version": "ai-deepseek-v4-flash-v1",
  "home_win": 0.4545,
  "draw": 0.2727,
  "away_win": 0.2727,
  "confidence": 0.65,
  "recommended_label": "home_win",
  "latency_ms": 2300,
  "error_code": null
}
```

---

## 2. Pro 模型调用示例

```
POST /api/ai-predictions/run?match_id=WC2026_A1_BRA_V_CRC&model_version=ai-deepseek-v4-pro-v1
```

**与 Flash 的区别：**

| 属性 | Flash | Pro |
|------|-------|-----|
| `model_id` | `deepseek-v4-flash` | `deepseek-v4-pro` |
| `cost_tier` | `low` | `high` |
| `latency_tier` | `fast` | `slow` |
| `ensemble_weight` | `0.33` | `0.67` |
| `role` | `fast_baseline` | `reasoning_strong` |

API 调用路径完全相同，仅 `model_id` 参数不同，DeepSeek API 根据此字段路由到不同模型。

---

## 3. Parse Error 示例

**场景：** AI 返回非法 JSON

```
AI 返回: "The home team has a strong chance. I'd say 60% home win."
```

**处理链路（`service.py:130-148`）：**

1. `_safe_json_parse(raw_response)` → 返回 `None`（非 JSON）
2. `parse_ai_response(raw_response)` → 尝试 markdown code block 提取 → 尝试 `{...}` 提取 → 均失败
3. 设置 `ai_pred.error_code = "parse_failed"`
4. 设置 `ai_pred.error_message = "parse_failed: Expecting value: line 1 column 1 (char 0)"`
5. `raw_response_text` 保留原始文本

**入库记录：**

```python
AIPrediction(
    match_id="WC2026_A1_BRA_V_CRC",
    provider="deepseek",
    model_id="deepseek-v4-flash",
    model_version="ai-deepseek-v4-flash-v1",
    raw_response_text="The home team has a strong chance. I'd say 60% home win.",
    parsed_home_win=None,       # 未解析
    parsed_draw=None,           # 未解析
    parsed_away_win=None,       # 未解析
    error_code="parse_failed",
    error_message="parse_failed: Expecting value: line 1 column 1 (char 0)",
)
```

**API 返回：**

```json
{
  "status": "error",
  "error_code": "parse_failed",
  "error_message": "parse_failed: Expecting value: ...",
  "latency_ms": 1800,
  "prediction_id": 43
}
```

---

## 4. No Key 示例

**场景：** 未设置 `DEEPSEEK_API_KEY` 环境变量

```
POST /api/ai-predictions/run?match_id=xxx&model_version=ai-deepseek-v4-flash-v1
```

**处理链路（`service.py:97-98`）：**

1. `provider.is_configured()` → `False`（`get_api_key()` 返回 `None`）
2. 返回 `{"status": "error", "error": "Provider deepseek not configured (missing API key)"}`

**AI 模型状态列表（`GET /api/ai-models`）：**

```json
{
  "enabled": false,
  "models": [
    {
      "provider": "deepseek",
      "model_id": "deepseek-v4-flash",
      "model_version": "ai-deepseek-v4-flash-v1",
      "enabled": false,
      "has_api_key": false,
      "status": "disabled_no_key",
      "disabled_no_key": true,
      "provider_health": "no_key"
    },
    {
      "provider": "deepseek",
      "model_id": "deepseek-v4-pro",
      "model_version": "ai-deepseek-v4-pro-v1",
      "enabled": false,
      "has_api_key": false,
      "status": "disabled_no_key",
      "disabled_no_key": true,
      "provider_health": "no_key"
    }
  ]
}
```

---

## 5. 入库字段示例（AIPrediction 全部字段）

```python
AIPrediction(
    id=42,                                           # 自增主键
    match_id="WC2026_A1_BRA_V_CRC",                  # FK → matches.id
    provider="deepseek",                             # 提供商名
    model_id="deepseek-v4-flash",                    # 模型 ID
    model_version="ai-deepseek-v4-flash-v1",         # 模型版本标识
    prompt_version="worldcup-ai-v1",                 # Prompt 版本
    input_snapshot_json={                            # 输入快照（用于审计）
        "match_id": "WC2026_A1_BRA_V_CRC",
        "model_version": "ai-deepseek-v4-flash-v1",
        "prompt_version": "worldcup-ai-v1",
        "system_probs": {"home_win": 0.52, "draw": 0.25, "away_win": 0.23},
        "market_probs": {"home_win": 0.48, "draw": 0.27, "away_win": 0.25},
    },
    raw_response_text='{"home_win":0.45,...}',       # 原始文本
    raw_response_json={"home_win": 0.45, ...},       # 解析后的 JSON
    parsed_home_win=0.4545,                          # 归一化后
    parsed_draw=0.2727,                              # 归一化后
    parsed_away_win=0.2727,                          # 归一化后
    confidence=0.65,                                 # AI 置信度
    risk_flags_json=["key_player_injury"],           # 风险标签
    key_factors_json=["home_advantage", "form"],     # 关键因素
    reason="Brazil has stronger squad depth...",      # AI 推理
    uncertainties_json=["market_divergence"],        # 不确定性
    disagreement_with_system="System says home, AI says home",  # 与系统分歧
    disagreement_with_market=None,                   # 与市场无分歧
    recommended_label="home_win",                    # 推荐标签
    created_at="2026-06-13T10:00:00Z",              # 创建时间
    locked_at="2026-06-13T10:00:00Z",               # 锁定时间
    is_pre_match_locked=True,                        # T-30 前锁定
    is_fallback_locked=False,                        # 非 fallback
    real_time_only=False,                            # 非实时
    error_code=None,                                 # 无错误
    error_message=None,                              # 无错误
    latency_ms=2300,                                 # 延迟 2.3s
    token_usage_json={"total_tokens": 850},          # Token 用量
)
```

---

## 6. T-30 锁定状态示例

**判断逻辑（`service.py:167-180`）：**

```python
now = datetime.now(timezone.utc)
kickoff = match.kickoff

if now < kickoff - timedelta(minutes=30):
    # 距开赛 > 30 分钟
    ai_pred.is_pre_match_locked = True    # ✅ 可作为评分依据
    ai_pred.locked_at = now
elif now < kickoff:
    # 距开赛 ≤ 30 分钟但未开赛
    ai_pred.is_fallback_locked = True     # ⚠️ 不可作为评分依据
    ai_pred.locked_at = now
# else: 已开赛
ai_pred.real_time_only = now >= kickoff   # ❌ 完全排除评分
```

### 三种状态对比

| 状态 | `is_pre_match_locked` | `is_fallback_locked` | `real_time_only` | 参与评分 |
|------|----------------------|---------------------|-----------------|---------|
| T-30 前预测 | `True` | `False` | `False` | ✅ 是 |
| T-30 内预测 | `False` | `True` | `False` | ❌ 否 |
| 开赛后预测 | `False` | `False` | `True` | ❌ 否 |

---

## 7. 概率归一化验证

**代码位置：** `parser.py:87-97`

```python
total = home_win + draw + away_win
if abs(total - 1.0) > 0.05:
    home_win /= total
    draw /= total
    away_win /= total
    warnings.append(f"normalized: original_sum={total:.4f}")
```

**验证示例：**

| 输入 | 原始 sum | 归一化后 | 触发条件 |
|------|---------|---------|---------|
| `{hw: 0.5, d: 0.3, aw: 0.3}` | 1.1 | `{0.4545, 0.2727, 0.2727}` | `abs(1.1 - 1.0) = 0.1 > 0.05` ✅ |
| `{hw: 0.5, d: 0.25, aw: 0.25}` | 1.0 | 不变 | `abs(1.0 - 1.0) = 0 ≤ 0.05` |
| `{hw: 0.4, d: 0.3, aw: 0.25}` | 0.95 | 不变 | `abs(0.95 - 1.0) = 0.05 ≤ 0.05` |
| `{hw: 0.6, d: 0.5, aw: 0.2}` | 1.3 | `{0.4615, 0.3846, 0.1538}` | `abs(1.3 - 1.0) = 0.3 > 0.05` ✅ |

**测试验证：** `test_ai_probabilities_normalize` 传入 sum=1.1，验证输出 sum ≈ 1.0

---

## 8. 概率非法拒绝验证

**代码位置：** `parser.py:82-84`

```python
if any(p < 0 or p > 1.5 for p in [home_win, draw, away_win]):
    warnings.append(f"probabilities_out_of_range: hw={home_win}, d={draw}, aw={away_win}")
    return None, warnings  # 直接拒绝，不归一化
```

**拒绝阈值：** 负值（`< 0`）或极端值（`> 1.5`）

**验证示例：**

| 输入 | 结果 | 原因 |
|------|------|------|
| `{hw: -0.5, d: 0.3, aw: 1.2}` | `None`（拒绝） | `hw < 0` |
| `{hw: 0.5, d: 0.3, aw: 2.0}` | `None`（拒绝） | `aw > 1.5` |
| `{hw: 0.0, d: 0.0, aw: 1.0}` | 成功解析 | 所有值在 [0, 1.5] 内 |
| `{hw: 1.5, d: 0.0, aw: 0.0}` | 成功解析 | 恰好等于 1.5 不拒绝 |
| `{hw: 0.0, d: 0.0, aw: 0.0}` | `None`（拒绝） | `sum ≤ 0`（`parser.py:89-91`） |

**测试验证：** `test_ai_invalid_probabilities_rejected` 传入 `hw=-0.5`，验证返回 `None`
