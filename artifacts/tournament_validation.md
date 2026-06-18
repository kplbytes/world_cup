# 全世界杯周期逻辑验证报告

> 验证日期：2026-06-13
> 验证范围：2026 FIFA World Cup 48 队赛制全周期逻辑
> 验证方法：代码路径分析 + 单元测试 + 赛制规则对照

---

## 1. Stage 覆盖情况

**代码位置：** `tournament/rules.py:22-30`

```python
STAGE_ORDER = [
    "group",           # 小组赛
    "round_of_32",     # 32 强
    "round_of_16",     # 16 强
    "quarter_final",   # 四分之一决赛
    "semi_final",      # 半决赛
    "third_place",     # 三四名决赛
    "final",           # 决赛
]
```

**对照 2026 FIFA 赛制：**

| 阶段 | 代码覆盖 | 比赛数 | 说明 |
|------|---------|-------|------|
| 小组赛 | ✅ `group` | 72 | 12 组 × 6 场 |
| 32 强赛 | ✅ `round_of_32` | 16 | 24 直接出线 + 8 个第三名 |
| 16 强赛 | ✅ `round_of_16` | 8 | — |
| 四分之一决赛 | ✅ `quarter_final` | 4 | — |
| 半决赛 | ✅ `semi_final` | 2 | — |
| 三四名决赛 | ✅ `third_place` | 1 | — |
| 决赛 | ✅ `final` | 1 | — |
| **合计** | **全覆盖** | **104** | — |

**显示名称覆盖（`STAGE_DISPLAY_NAMES`）：**

```python
"group": "小组赛"
"round_of_32": "32强"
"round_of_16": "16强"
"quarter_final": "四分之一决赛"
"semi_final": "半决赛"
"third_place": "三四名决赛"
"final": "决赛"
```

**测试验证：** `test_stage_enum_complete` — 验证 `required == set(STAGE_ORDER)`

---

## 2. Bracket 示例

**API 端点：** `GET /api/tournament/bracket`

**代码位置：** `tournament/bracket.py:120-145`

**`generate_bracket` 输出结构：**

```json
{
  "round_of_32": [
    {
      "match_position": 1,
      "stage": "round_of_32",
      "home_source": "A1",
      "away_source": "C2",
      "home_team": {"id": "BRA", "short_name": "Brazil", ...},
      "away_team": {"id": "MEX", "short_name": "Mexico", ...}
    },
    // ... 16 matches
  ],
  "round_of_16": [
    {
      "match_position": 1,
      "stage": "round_of_16",
      "home_source": "Winner M1",
      "away_source": "Winner M2",
      "home_team": null,  // 尚未确定
      "away_team": null   // 尚未确定
    },
    // ... 8 matches
  ],
  "quarter_final": [],
  "semi_final": [],
  "third_place": [],
  "final": []
}
```

**注意：** R16 之后的轮次在无 `knockout_results` 时为空列表，`_generate_next_round` 仅在有比赛结果时填充。

---

## 3. Placeholder Match 示例

**代码位置：** `models.py:59` — `is_placeholder_match` 字段

**创建示例（`test_placeholder_match_creation`）：**

```python
Match(
    id="KO_P1",
    home_team_id=None,              # 未确定
    away_team_id=None,              # 未确定
    kickoff=datetime.now(timezone.utc) + timedelta(days=30),
    status="scheduled",
    source="tournament",
    stage="round_of_32",
    round_name="32强赛",
    bracket_position=1,
    home_team_source="A1",          # 小组 A 第 1 名
    away_team_source="C2",          # 小组 C 第 2 名
    is_placeholder_match=True,
)
```

**Placeholder 特征：**
- `home_team_id = None` / `away_team_id = None`
- `home_team_source` / `away_team_source` 使用 `"A1"`、`"C2"` 等来源标识
- `is_placeholder_match = True`
- 小组赛结束后根据排名填充具体球队

---

## 4. Projections 示例

**API 端点：** `GET /api/tournament/projections`

**代码位置：** `tournament/qualification.py:22-112`

**TeamProjection 各阶段概率：**

```json
{
  "projections": [
    {
      "team_id": "BRA",
      "group_qualify": 0.92,
      "round_of_32": 0.85,
      "round_of_16": 0.65,
      "quarter_final": 0.45,
      "semi_final": 0.25,
      "final": 0.12,
      "champion": 0.06
    },
    {
      "team_id": "FRA",
      "group_qualify": 0.95,
      "round_of_32": 0.88,
      "round_of_16": 0.70,
      "quarter_final": 0.50,
      "semi_final": 0.30,
      "final": 0.15,
      "champion": 0.08
    },
    // ... 48 teams sorted by champion probability descending
  ]
}
```

**计算方式：**
- `group_qualify`：来自 `QualificationPrediction.qualify_probability`（基于 simulation 模块）
- R32 → Champion：Monte Carlo 模拟，每轮根据 Elo 差计算胜率
- 概率单调递减：`champion ≤ final ≤ semi_final ≤ quarter_final ≤ ...`

---

## 5. Champion Probability 示例

**API 端点：** `POST /api/tournament/simulate?iterations=10000`

**代码逻辑（`qualification.py:56-96`）：**

```python
for _ in range(iterations):
    # Step 1: 确定出线队伍
    qualified = [t for t in team_ids if rng.random() < qualification_probs[t]]
    r32_counts[t] += 1

    # Step 2: 如果 32 队出线，模拟淘汰赛
    if len(qualified) >= 32:
        qualified_sorted = sorted(qualified, key=lambda t: team_elos[t], reverse=True)
        current_round = qualified_sorted[:32]

        # 逐轮模拟
        r32_winners = _simulate_round(rng, current_round, ...)
        r16_winners = _simulate_round(rng, r32_winners, ...)
        qf_winners = _simulate_round(rng, r16_winners, ...)
        sf_winners = _simulate_round(rng, qf_winners, ...)
        champion = _simulate_round(rng, sf_winners, ...)
        champion_counts[champion[0]] += 1
```

**Champion 概率计算：**

```
champion_prob = champion_counts[team_id] / iterations
```

**示例输出（iterations=10000）：**

| 球队 | Elo | group_qualify | R32 | R16 | QF | SF | Final | Champion |
|------|-----|--------------|-----|-----|----|----|-------|----------|
| France | 2050 | 0.95 | 0.88 | 0.70 | 0.50 | 0.30 | 0.15 | 0.08 |
| Brazil | 2020 | 0.92 | 0.85 | 0.65 | 0.45 | 0.25 | 0.12 | 0.06 |
| Argentina | 2000 | 0.90 | 0.80 | 0.58 | 0.38 | 0.20 | 0.10 | 0.04 |
| Japan | 1720 | 0.70 | 0.35 | 0.15 | 0.05 | 0.02 | 0.01 | 0.002 |

---

## 6. 当前规则限制

### 6.1 对阵图使用 Elo 排序而非真实 FIFA 抽签规则

**代码位置：** `qualification.py:67-68`

```python
# Sort by Elo for seeding (simplified - real bracket would use group positions)
qualified_sorted = sorted(qualified, key=lambda t: team_elos.get(t, 1500), reverse=True)
```

**问题：**
- 真实 2026 世界杯淘汰赛对阵遵循 FIFA 官方抽签规则
- 淘汰赛路径由小组排名 + 抽签决定，避免同组提前相遇
- 当前实现按 Elo 从高到低排列，1 号 vs 32 号、2 号 vs 31 号...
- 这导致强队过早或过晚相遇，不符合真实赛制

### 6.2 第三名排名规则简化

**代码位置：** `bracket.py:25-43`

```python
ROUND_OF_32_BRACKET = [
    (1, "A1", "C/D/E/F3"),       # 简化：从 C/D/E/F 中选最佳第三
    (2, "B2", "C2"),
    # ...
]
```

**问题：**
- 真实赛制中，8 个最佳第三名的分配取决于哪些组的第三名出线
- FIFA 有约束表（constraint table），确保同组不提前相遇
- 当前代码使用硬编码的简化分配规则，可能不正确

### 6.3 `_generate_next_round` 的默认值问题

**代码位置：** `bracket.py:163-164`

```python
home_team = match1.get("home_team") or match1.get("winner")
away_team = match2.get("home_team") or match2.get("winner")
```

**问题：** 如果淘汰赛结果未填入，`winner` 为空，默认使用 `home_team`（主队），这可能不正确。

### 6.4 无三四名决赛逻辑

**当前状态：** `STAGE_ORDER` 包含 `"third_place"`，但 `generate_bracket` 和 `_simulate_round` 均未实现三四名决赛的单独对阵生成。

---

## 7. 重要提醒

> **当前淘汰赛路径为简化模拟版本，真实对阵规则需根据官方赛制最终确认。**

具体而言：

1. **小组赛阶段**：可用。12 组 4 队单循环、前 2 名 + 8 个最佳第三名出线的逻辑正确。
2. **淘汰赛阶段**：仅作参考。对阵路径、种子排位、同组回避等规则均为简化实现。
3. **Monte Carlo 模拟**：Elo 差 → 胜率的公式（`1/(1+10^(-delta*3))`）为简化模型，未考虑主客场（世界杯为中立场地）、赛事压力等因素。
4. **加时赛/点球**：简化为 70% 在 90 分钟内决出 + 30% 进入加时，加时赛中优势缩小为 `0.5 + (win_prob - 0.5) * 0.3`。

---

## 8. 是否适合用于真实世界杯预测

| 阶段 | 适合程度 | 说明 |
|------|---------|------|
| 小组赛 | ✅ 可用 | 积分规则、排名规则正确；出线概率基于 Monte Carlo 模拟，方法合理 |
| 淘汰赛对阵 | ⚠️ 仅参考 | 对阵路径为简化版本，不反映真实抽签 |
| 淘汰赛胜率 | ⚠️ 仅参考 | Elo 单因子模型，未考虑战术、心理、伤病 |
| 冠军概率 | ⚠️ 仅参考 | 多层简化累积，误差在后期轮次放大 |
| 三四名决赛 | ❌ 不可用 | 无专门逻辑 |

**建议：** 小组赛阶段可使用本系统预测；淘汰赛阶段需标注"简化模拟，仅供参考"，并在 UI 中明确提示。
