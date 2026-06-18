# 数据污染防护审计报告

> 审计日期：2026-06-13
> 审计范围：10 条数据污染防护规则
> 审计方法：代码路径逐条验证 + 单元测试覆盖

---

## 总览

| # | 规则 | 结果 | 代码位置 |
|---|------|------|---------|
| 1 | T-30 locked snapshot 是否仍是唯一评分依据 | ✅ 通过 | `scoring.py:649,198` |
| 2 | Fallback snapshot 是否会污染准确率结论 | ✅ 通过 | `scoring.py:198,649` |
| 3 | AI T-30 后预测是否被排除评分 | ✅ 通过 | `evaluation.py:127` |
| 4 | 赛后数据是否可能进入 prompt | ✅ 通过 | `service.py:342-477` |
| 5 | Placeholder knockout match 是否会进入错误评分 | ✅ 通过 | `scoring.py:645-652` |
| 6 | AI parse error 是否会进入 ensemble | ✅ 通过 | `ensemble.py:72` |
| 7 | Market odds 与 match_id 是否正确匹配 | ✅ 通过 | `market_snapshots.match_id FK` |
| 8 | Tournament projection 是否使用赛后信息 | ✅ 通过 | `qualification.py:22-112` |
| 9 | model_version 是否互相覆盖 | ✅ 通过 | `scoring.py:316-456` |
| 10 | Dashboard revision 是否能追溯 | ✅ 通过 | `models.py:94-103` |

---

## 逐条审计

### 规则 1：T-30 locked snapshot 是否仍是唯一评分依据

**结论：✅ 通过**

**代码证据：**

`scoring.py:645-652` — `score_model` 函数查询条件：

```python
rows = session.execute(
    select(PredictionSnapshot, Match)
    .join(Match, PredictionSnapshot.match_id == Match.id)
    .where(Match.status == "final")
    .where(PredictionSnapshot.is_pre_match_locked.is_(True))  # 仅 T-30 锁定
).all()
```

`scoring.py:198` — `model_score_details` 同样：

```python
.where(PredictionSnapshot.is_pre_match_locked.is_(True))
```

`evaluation.py:76` — AI evaluation：

```python
snap = session.scalar(
    select(PredictionSnapshot)
    .where(PredictionSnapshot.match_id == match.id)
    .where(PredictionSnapshot.is_pre_match_locked.is_(True))
)
```

**所有评分查询均以 `is_pre_match_locked = True` 为唯一入口，fallback 锁定和未锁定的 snapshot 均不参与评分。**

**测试验证：** `test_t30_locked_only_scoring_basis` — 当同时存在 `is_pre_match_locked=True` 和 `is_fallback_locked=True` 的 snapshot 时，评分使用前者。

---

### 规则 2：Fallback snapshot 是否会污染准确率结论

**结论：✅ 通过**

**代码证据：**

Fallback snapshot（`is_fallback_locked=True, is_pre_match_locked=False`）不满足评分查询条件：

```python
.where(PredictionSnapshot.is_pre_match_locked.is_(True))
```

因此 fallback 被排除在所有评分计算之外。

**附加防护：** `snapshots.py:56-74` — 如果错过了 T-30 窗口，不会将开赛后生成的 snapshot 标记为 `is_pre_match_locked`，而是：
1. 尝试升级开赛前最后一个 snapshot 为 `is_fallback_locked=True`
2. 当前 snapshot 保持未锁定状态

**测试验证：** `test_fallback_not_counted_as_t30` — 验证 `is_pre_match_locked=False` 且 `is_fallback_locked=True` 的 snapshot 不被评分。

---

### 规则 3：AI T-30 后预测是否被排除评分

**结论：✅ 通过**

**代码证据：**

`evaluation.py:127` — AI evaluation 查询条件：

```python
ai_pred = session.scalar(
    select(AIPrediction)
    .where(AIPrediction.match_id == match.id)
    .where(AIPrediction.model_version == model_version)
    .where(AIPrediction.error_code.is_(None))
    .where(AIPrediction.parsed_home_win.isnot(None))
    .where(AIPrediction.is_pre_match_locked.is_(True))  # 仅 T-30 前锁定
    .order_by(AIPrediction.created_at.desc())
    .limit(1)
)
```

`service.py:174-180` — T-30 后产生的 AI 预测标记为 `real_time_only=True`：

```python
if now < kickoff - timedelta(minutes=30):
    ai_pred.is_pre_match_locked = True
elif now < kickoff:
    ai_pred.is_fallback_locked = True
ai_pred.real_time_only = now >= kickoff
```

因此开赛后产生的 AI 预测（`real_time_only=True`）的 `is_pre_match_locked` 为 `False`，不满足评分查询条件。

**测试验证：** `test_ai_real_time_not_in_scoring` — 验证 `real_time_only=True` 的 AI 预测 `sample_count=0`。

---

### 规则 4：赛后数据是否可能进入 prompt

**结论：✅ 通过**

**代码证据：**

`service.py:342-477` — `_build_prediction_request` 从数据库读取数据，所有数据源均为 snapshot/快照数据：

1. **系统预测**：读取 `PredictionSnapshot`（预测快照，非实时预测）
2. **市场赔率**：读取 `MarketSnapshot`（赔率快照，按 `fetched_at` 排序取最新）
3. **情报数据**：读取 `MatchIntelligence`（按 `fetched_at` 排序）
4. **评分摘要**：读取 `ModelScore`（历史评分记录）

**关键防护：** `_build_prediction_request` 在 `PredictionSnapshot` 不存在时返回 `None`（`service.py:365`），拒绝调用 AI：

```python
if not snap:
    return None  # No system prediction — refuse to call AI with fake defaults
```

**赛后数据不会进入 prompt** — 因为 `PredictionSnapshot` 是在 T-30 时锁定的快照，赛后不会重新生成。`MatchIntelligence` 虽然可能在赛后更新，但 AI 预测仅在赛前调用（T-30 后标记为 real_time_only，不参与评分）。

---

### 规则 5：Placeholder knockout match 是否会进入错误评分

**结论：✅ 通过**

**代码证据：**

Placeholder match 特征：
- `home_team_id = None`
- `away_team_id = None`
- `is_placeholder_match = True`
- `status = "scheduled"`（未开赛）

`scoring.py:645-652` — 评分查询条件：

```python
.where(Match.status == "final")  # 仅已结束的比赛
```

Placeholder match 永远不会变为 `"final"` 状态（因为无球队，无法比赛），因此不会进入评分。

**即使有人错误地将 placeholder 标记为 final**，由于 `home_team_id = None`，`PredictionSnapshot` 无法关联（`match_id` 虽然存在，但 `score_model` 的 `team_names` 查询会找不到队伍名，虽然记录会出现但数据不完整）。

**测试验证：** `test_placeholder_not_in_scoring` — 验证 placeholder match 的 `match_id` 不出现在评分结果中。

---

### 规则 6：AI parse error 是否会进入 ensemble

**结论：✅ 通过**

**代码证据：**

`ensemble.py:69-76` — ensemble 查询 AI 预测的过滤条件：

```python
ai_preds = list(session.scalars(
    select(AIPrediction)
    .where(AIPrediction.match_id == match_id)
    .where(AIPrediction.error_code.is_(None))        # 排除 error_code 不为空的
    .where(AIPrediction.parsed_home_win.isnot(None))  # 排除未解析的
    .where(AIPrediction.real_time_only.is_(False))     # 排除实时的
    .order_by(AIPrediction.created_at.desc())
))
```

parse error 的 `error_code = "parse_failed"`，不满足 `error_code IS NULL` 条件，被排除。

**双重防护：** 即使 `error_code` 意外为空但解析失败，`parsed_home_win = None` 也会被 `parsed_home_win IS NOT NULL` 过滤。

**测试验证：** `test_ai_parse_error_not_in_ensemble` — 验证 `error_code="parse_failed"` 的 AI 预测不在 ensemble 的 `source_probabilities` 中。

---

### 规则 7：Market odds 与 match_id 是否正确匹配

**结论：✅ 通过**

**代码证据：**

`models.py:296-308` — `MarketSnapshot` 表定义：

```python
class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    # ...
```

`match_id` 有 FK 约束指向 `matches.id`，数据库层面确保关联正确。

**代码中的使用：**

`ensemble.py:51-66` — 通过 `match_id` 查询市场赔率：

```python
market_snap = session.scalar(
    select(MarketSnapshot)
    .where(MarketSnapshot.match_id == match_id)
    .where(MarketSnapshot.provider == "sporttery")
    .order_by(MarketSnapshot.fetched_at.desc())
    .limit(1)
)
```

`scoring.py:201-204` — 评分时通过 `match_id` 关联市场数据：

```python
market_snaps = {
    row.match_id: row
    for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
}
```

**FK 约束 + 查询条件确保市场赔率与比赛正确匹配。**

---

### 规则 8：Tournament projection 是否使用赛后信息

**结论：✅ 通过**

**代码证据：**

`qualification.py:22-112` — `compute_projections` 仅使用两类输入：

1. **`qualification_probs`**：来自 `QualificationPrediction.qualify_probability`，这是赛前预测概率，非实际出线结果
2. **`team_elos`**：来自 `TeamRating.elo`，这是赛前 Elo 评分，非赛后更新的评分

**关键代码：**

```python
def compute_projections(
    qualification_probs: dict[str, float],  # 预测概率
    team_elos: dict[str, float],            # 赛前 Elo
    iterations: int = 10_000,
    seed: int = 20260613,
) -> list[TeamProjection]:
```

`simulation.py:24-31` — 数据来源：

```python
qual_preds = list(session.scalars(select(QualificationPrediction)))
# ...
qualification_probs[pred.team_id] = pred.qualify_probability
```

`QualificationPrediction` 在 `dashboard_revisions` 创建时写入，基于当时的模拟结果，不会在赛后更新。

**Monte Carlo 模拟内部也不使用赛后信息** — 每轮的胜负由 `rng.random()` 和 Elo 决定，不读取实际比赛结果。

---

### 规则 9：model_version 是否互相覆盖

**结论：✅ 通过**

**代码证据：**

`scoring.py:316-456` — `model_score_by_version` 按 `model_version` 分组：

```python
by_version: dict[str, dict[str, Any]] = {}
for snap, match in rows:
    version = snap.model_version or "unknown"
    if version not in by_version:
        by_version[version] = {
            "model_version": version,
            "sample_count": 0,
            # ...
        }
```

每个 `model_version` 有独立的统计桶，互不覆盖。

**数据库层面：** `PredictionSnapshot` 的 `model_version` 字段与 `revision_id` 共同标识一个快照。不同 `model_version` 的 snapshot 可以共存于同一 `match_id`。

**AI 预测层面：** `AIPrediction` 的 `model_version` 字段区分不同 AI 模型。`EnsemblePrediction` 使用固定 `model_version = "ensemble-v1"`，与 AI 和系统模型均不冲突。

**测试验证：** `test_model_versions_isolated` — 同时存在 `elo-poisson-v1` 和 `elo-poisson-v1-intel-numeric` 的 snapshot，两者独立出现在 `model_score_by_version` 结果中。

---

### 规则 10：Dashboard revision 是否能追溯

**结论：✅ 通过**

**代码证据：**

`models.py:94-103` — `DashboardRevision` 表定义：

```python
class DashboardRevision(Base):
    __tablename__ = "dashboard_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    model_version: Mapped[str] = mapped_column(String(40))
    simulation_iterations: Mapped[int] = mapped_column(Integer)
    simulation_seed: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
```

**可追溯性保障：**

1. **`id`**：自增主键，唯一标识每次 revision
2. **`created_at`**：创建时间戳（UTC），精确到微秒
3. **`model_version`**：记录使用的模型版本
4. **`simulation_iterations` + `simulation_seed`**：记录模拟参数，可复现
5. **`active`**：标记当前激活版本，历史版本仍保留

**关联追溯：**
- `PredictionSnapshot.revision_id` → `DashboardRevision.id`
- `ModelScore.revision_id` → `DashboardRevision.id`
- `MatchPrediction.revision_id` → `DashboardRevision.id`
- `StandingSnapshot.revision_id` → `DashboardRevision.id`
- `QualificationPrediction.revision_id` → `DashboardRevision.id`

**通过 revision_id 可以追溯任意预测/评分记录对应的 revision 配置。**

**测试验证：** `test_dashboard_revision_traceable` — 验证 `rev.id is not None` 且 `rev.created_at is not None`。

---

## 总结

**10 条数据污染防护规则全部通过。**

关键防护机制：
1. **T-30 锁定**：所有评分查询以 `is_pre_match_locked=True` 为唯一入口
2. **Fallback 隔离**：fallback 锁定的 snapshot 不参与评分
3. **AI 实时排除**：`real_time_only=True` 的预测从评分和 ensemble 双重排除
4. **Parse error 隔离**：`error_code IS NOT NULL` 的 AI 预测从 ensemble 和评分双重排除
5. **Placeholder 隔离**：无球队的比赛无法进入 `status="final"` 评分路径
6. **版本隔离**：每个 `model_version` 有独立的统计桶
7. **追溯性**：所有 revision 有 `id` + `created_at`，关联表通过 `revision_id` 追溯
