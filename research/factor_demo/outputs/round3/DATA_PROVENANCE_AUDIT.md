# 数据溯源审计报告 - 第三轮验证

**审计时间**: 2026-06-15 17:41 UTC
**审计人**: 自动化脚本

---

## 1. 原始文件信息

| 项目 | 值 |
|------|-----|
| 文件路径 | `data/external/international_results.csv` |
| SHA256 | `8043b32b7ebd7915397154a9b8cca37e86f30b8549b00bbbd41e67a90ebc9b41` |
| 文件大小 | 3,724,383 bytes |
| 总记录数 | 49,477 |
| 数据来源 | Kaggle International Football Results |
| 最大真实赛果日期 | 2026-06-13 |

## 2. 2026年记录分类统计

| 记录类型 | 数量 | 说明 |
|---------|------|------|
| result（已完赛） | 319 | 有真实比分，日期 <= 今天 |
| fixture（赛程/未赛） | 64 | 无比分或日期 > 今天 |
| simulated | 0 | 无模拟数据 |
| seed | 0 | 无种子数据 |
| **合计** | **383** | |

## 3. 2026世界杯记录详情

| 类型 | 数量 |
|------|------|
| 世界杯总记录 | 72 |
| 已完赛（有比分） | 8 |
| 赛程/未赛（NA比分） | 64 |

### 已完赛世界杯比赛

| 日期 | 主队 | 客队 | 比分 | 城市 | 国家 |
|------|------|------|------|------|------|
| 2026-06-11 | Mexico | South Africa | 2-0 | Mexico City | Mexico |
| 2026-06-11 | South Korea | Czech Republic | 2-1 | Zapopan | Mexico |
| 2026-06-12 | Canada | Bosnia and Herzegovina | 1-1 | Toronto | Canada |
| 2026-06-12 | United States | Paraguay | 4-1 | Inglewood | United States |
| 2026-06-13 | Qatar | Switzerland | 1-1 | Santa Clara | United States |
| 2026-06-13 | Brazil | Morocco | 1-1 | East Rutherford | United States |
| 2026-06-13 | Haiti | Scotland | 0-1 | Foxborough | United States |
| 2026-06-13 | Australia | Turkey | 2-0 | Vancouver | Canada |

### 未赛世界杯赛程（排除）

| 日期 | 主队 | 客队 | 城市 | 国家 |
|------|------|------|------|------|
| 2026-06-14 | Germany | Curaçao | Houston | United States |
| 2026-06-14 | Ivory Coast | Ecuador | Philadelphia | United States |
| 2026-06-14 | Netherlands | Japan | Arlington | United States |
| 2026-06-14 | Sweden | Tunisia | Guadalupe | Mexico |
| 2026-06-15 | Belgium | Egypt | Seattle | United States |
| 2026-06-15 | Iran | New Zealand | Inglewood | United States |
| 2026-06-15 | Spain | Cape Verde | Atlanta | United States |
| 2026-06-15 | Saudi Arabia | Uruguay | Miami Gardens | United States |
| 2026-06-16 | France | Senegal | East Rutherford | United States |
| 2026-06-16 | Iraq | Norway | Foxborough | United States |
| 2026-06-16 | Argentina | Algeria | Kansas City | United States |
| 2026-06-16 | Austria | Jordan | Santa Clara | United States |
| 2026-06-17 | Portugal | DR Congo | Houston | United States |
| 2026-06-17 | Uzbekistan | Colombia | Mexico City | Mexico |
| 2026-06-17 | England | Croatia | Arlington | United States |
| 2026-06-17 | Ghana | Panama | Toronto | Canada |
| 2026-06-18 | Czech Republic | South Africa | Atlanta | United States |
| 2026-06-18 | Mexico | South Korea | Zapopan | Mexico |
| 2026-06-18 | Switzerland | Bosnia and Herzegovina | Inglewood | United States |
| 2026-06-18 | Canada | Qatar | Vancouver | Canada |
| 2026-06-19 | Scotland | Morocco | Foxborough | United States |
| 2026-06-19 | Brazil | Haiti | Philadelphia | United States |
| 2026-06-19 | United States | Australia | Seattle | United States |
| 2026-06-19 | Turkey | Paraguay | Santa Clara | United States |
| 2026-06-20 | Germany | Ivory Coast | Toronto | Canada |
| 2026-06-20 | Ecuador | Curaçao | Kansas City | United States |
| 2026-06-20 | Netherlands | Sweden | Houston | United States |
| 2026-06-20 | Tunisia | Japan | Guadalupe | Mexico |
| 2026-06-21 | Belgium | Iran | Inglewood | United States |
| 2026-06-21 | New Zealand | Egypt | Vancouver | Canada |
| 2026-06-21 | Spain | Saudi Arabia | Atlanta | United States |
| 2026-06-21 | Uruguay | Cape Verde | Miami Gardens | United States |
| 2026-06-22 | France | Iraq | Philadelphia | United States |
| 2026-06-22 | Norway | Senegal | East Rutherford | United States |
| 2026-06-22 | Argentina | Austria | Arlington | United States |
| 2026-06-22 | Jordan | Algeria | Santa Clara | United States |
| 2026-06-23 | Portugal | Uzbekistan | Houston | United States |
| 2026-06-23 | Colombia | DR Congo | Zapopan | Mexico |
| 2026-06-23 | England | Ghana | Foxborough | United States |
| 2026-06-23 | Panama | Croatia | Toronto | Canada |
| 2026-06-24 | Mexico | Czech Republic | Mexico City | Mexico |
| 2026-06-24 | South Africa | South Korea | Guadalupe | Mexico |
| 2026-06-24 | Canada | Switzerland | Vancouver | Canada |
| 2026-06-24 | Bosnia and Herzegovina | Qatar | Seattle | United States |
| 2026-06-24 | Scotland | Brazil | Miami Gardens | United States |
| 2026-06-24 | Morocco | Haiti | Atlanta | United States |
| 2026-06-25 | United States | Turkey | Inglewood | United States |
| 2026-06-25 | Paraguay | Australia | Santa Clara | United States |
| 2026-06-25 | Curaçao | Ivory Coast | Philadelphia | United States |
| 2026-06-25 | Ecuador | Germany | East Rutherford | United States |
| 2026-06-25 | Japan | Sweden | Arlington | United States |
| 2026-06-25 | Tunisia | Netherlands | Kansas City | United States |
| 2026-06-26 | Egypt | Iran | Seattle | United States |
| 2026-06-26 | New Zealand | Belgium | Vancouver | Canada |
| 2026-06-26 | Cape Verde | Saudi Arabia | Houston | United States |
| 2026-06-26 | Uruguay | Spain | Zapopan | Mexico |
| 2026-06-26 | Norway | France | Foxborough | United States |
| 2026-06-26 | Senegal | Iraq | Toronto | Canada |
| 2026-06-27 | Algeria | Austria | Kansas City | United States |
| 2026-06-27 | Jordan | Argentina | Arlington | United States |
| 2026-06-27 | Colombia | Portugal | Miami Gardens | United States |
| 2026-06-27 | DR Congo | Uzbekistan | Atlanta | United States |
| 2026-06-27 | Panama | England | East Rutherford | United States |
| 2026-06-27 | Croatia | Ghana | Philadelphia | United States |

## 4. 数据冻结决策

- **冻结日期**: 2025-12-31
- **原因**: 2026年数据包含未完赛赛程，不可用于回测评分
- **2026年已验证结果**: 319 场可用于特征计算（但不用于评分）
- **2026年排除记录**: 64 场

### 2026世界杯前瞻Shadow验证方案

- 2026世界杯改为前瞻Shadow验证：赛前生成不可修改的预测快照，赛后再评分
- 当前不得使用2026世界杯已有结果反向回测
- 预测快照须在比赛开球前生成，包含时间戳和哈希签名

## 5. 回测准入条件

只有满足以下所有条件的比赛才能进入回测：

1. `record_type = result`
2. `result_verified = true`
3. `result_available_at <= evaluation_as_of`
4. 比赛日期 <= 2025-12-31（冻结日期）
5. 有完整比分（非NA）

## 6. 第二轮盲测指标作废声明

第二轮验证中基于2026年数据生成的盲测指标（包括2026盲测集Brier、世界杯盲测退化等）
因数据包含未完赛赛程和不可验证结果，**全部作废**。
第三轮验证仅使用截至2025-12-31的已验证数据。

---

*审计完成时间: 2026-06-15 17:41 UTC*