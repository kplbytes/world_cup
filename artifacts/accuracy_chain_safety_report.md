# 准确率链路安全报告

## 修复内容

### 1. T-30 锁定逻辑提取

**问题：** `service.py` 和 `ensemble.py` 各有一份几乎相同的 T-30 锁定判断逻辑，维护时容易遗漏。

**修复：** 新建 `backend/app/ai/lock_status.py`，提取 `compute_match_lock_status()` 共享函数。

**返回值：**
- `is_pre_match_locked`: T-30 前可锁定
- `is_fallback_locked`: T-30 内可锁定
- `real_time_only`: 开赛后实时
- `locked_at`: 锁定时间
- `participates_in_model_score`: 是否参与评分（赛后=False）

**修改文件：** `backend/app/ai/lock_status.py`（新建）、`backend/app/ai/service.py`、`backend/app/ai/ensemble.py`

### 2. most_likely_score 默认值修复

**问题：** 无 scorelines 时默认 "1-0"，会污染 AI prompt。

**修复：** 改为 "unknown"。

**修改文件：** `backend/app/ai/service.py`

### 3. AI 概率校验（详见 ai_probability_validation.md）

非法概率不进入 ensemble，不参与 model-score。

### 4. run-all 防重复调用

**问题：** `only_missing=true` 时只检查是否有任何成功预测，不区分模型版本。

**修复：** 按模型版本逐一检查，新增 `retry_failed` 参数，跳过时记录 reason。

**修改文件：** `backend/app/ai/service.py`

### 5. 并发 AI 调用

**问题：** 多模型顺序调用，延迟叠加。

**修复：** 使用 `asyncio.gather` + 信号量并发控制，一个模型失败不影响其他。

**修改文件：** `backend/app/ai/service.py`

## 新增测试

`tests/test_ai_improvements.py`（20 个测试）：
- 概率校验 6 个
- 锁定状态 5 个
- most_likely_score 2 个
- 并发调用 3 个
- 防重复调用 4 个

## 剩余风险

- `participates_in_model_score` 字段目前仅计算但未被评分逻辑主动使用。评分逻辑仍依赖 `is_pre_match_locked` 和 `is_fallback_locked` 来判断。后续应统一使用 `participates_in_model_score`。
- ensemble 的 `EnsemblePrediction` 模型缺少 `is_fallback_locked` 和 `real_time_only` 字段，与 `AIPrediction` 不一致。

## 对准确率链路的影响

**正面影响**：
1. 非法概率不再污染 ensemble
2. 锁定逻辑统一，减少维护遗漏风险
3. "1-0" 默认值不再污染 AI prompt
4. 防重复调用避免同一模型被重复计分

**不影响**：现有 P0/P1/P2/P2+ 功能继续正常工作。
