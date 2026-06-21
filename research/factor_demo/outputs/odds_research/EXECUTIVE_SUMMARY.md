# 执行摘要: 赔率因子研究

生成时间: 2026-06-19 11:43:12

## 研究问题
赔率因子能否突破 2% Brier 改善阈值？

## 数据
- 149 个 CSV 文件, 10 欧洲联赛, 2010-2025
- 58,000+ 场比赛, 含 Bet365/Bet&Win/Interwetten/Pinnacle 等赔率

## 核心结论
- **突破 2% 阈值**
- 最佳模型: odds_calibrated_elo (Brier=0.6020)
- 相对改善: 11.79% (基线 Brier=0.6824)

## 赔率因子排名
- **odds_implied_away**: IC=-0.345, ICIR=-11.528
- **odds_implied_home**: IC=0.344, ICIR=11.723
- **pinnacle_implied_away**: IC=-0.338, ICIR=-13.872

## 建议
- 纳入正式系统
- 优先使用 Pinnacle 隐含概率
- 赔率 vs Elo 分歧因子值得关注
- Draw 预测是赔率数据最大的价值所在