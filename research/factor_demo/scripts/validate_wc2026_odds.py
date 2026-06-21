#!/usr/bin/env python3
"""用赔率因子验证 2026 世界杯已完成比赛的预测效果"""
import json
import numpy as np

# 2026 世界杯已完成 24 场比赛的赔率数据（来源：britishgambler.co.uk, nowscore.co, edhat.com, bwin.com）
# 格式: (home, away, home_score, away_score, odds_home, odds_draw, odds_away)
matches = [
    # Matchday 1
    ("Mexico", "South Africa", 2, 0, 1.36, 4.50, 8.50),
    ("Korea Republic", "Czechia", 2, 1, 2.60, 3.10, 2.88),
    ("Canada", "Bosnia & Herzegovina", 1, 1, 1.84, 3.70, 4.50),
    ("United States", "Paraguay", 4, 1, 1.50, 4.00, 6.50),
    ("Qatar", "Switzerland", 1, 1, 13.00, 6.80, 1.24),
    ("Brazil", "Morocco", 1, 1, 1.68, 3.85, 5.60),
    ("Haiti", "Scotland", 0, 1, 6.00, 4.40, 1.56),
    ("Australia", "Türkiye", 2, 0, 5.00, 3.80, 1.75),
    ("Germany", "Curaçao", 7, 1, 1.04, 20.00, 50.00),
    ("Netherlands", "Japan", 2, 2, 2.06, 3.70, 3.65),
    ("Cote d'Ivoire", "Ecuador", 1, 0, 3.65, 2.88, 2.44),
    ("Sweden", "Tunisia", 5, 1, 1.75, 3.60, 5.00),
    ("Spain", "Cabo Verde", 0, 0, 1.10, 10.00, 26.00),
    ("Belgium", "Egypt", 1, 1, 1.60, 3.80, 6.00),
    ("Saudi Arabia", "Uruguay", 1, 1, 4.50, 3.40, 1.85),
    ("IR Iran", "New Zealand", 2, 2, 2.30, 3.10, 3.30),
    ("France", "Senegal", 3, 1, 1.99, 3.05, 5.30),
    ("Iraq", "Norway", 1, 4, 15.00, 7.40, 1.24),
    ("Argentina", "Algeria", 3, 0, 1.46, 4.70, 9.00),
    ("Austria", "Jordan", 3, 1, 1.38, 5.40, 10.50),
    ("Portugal", "Congo DR", 1, 1, 1.32, 5.90, 13.00),
    ("England", "Croatia", 4, 2, 1.78, 3.85, 5.50),
    ("Ghana", "Panama", 1, 0, 2.30, 3.45, 3.55),
    ("Uzbekistan", "Colombia", 1, 3, 10.50, 5.00, 1.40),
]

def implied_prob(odds_h, odds_d, odds_a):
    """计算隐含概率（去除博彩公司利润率）"""
    raw_h = 1.0 / odds_h
    raw_d = 1.0 / odds_d
    raw_a = 1.0 / odds_a
    overround = raw_h + raw_d + raw_a
    return raw_h / overround, raw_d / overround, raw_a / overround

def brier_score(probs, actual):
    """计算多分类 Brier Score"""
    n = len(probs)
    bs = 0.0
    for i in range(n):
        p_h, p_d, p_a = probs[i]
        a_h, a_d, a_a = actual[i]
        bs += (p_h - a_h)**2 + (p_d - a_d)**2 + (p_a - a_a)**2
    return bs / n

# 计算每场比赛的隐含概率
print("=" * 90)
print(f"{'比赛':<40s} {'比分':>4s} {'赔率预测':>6s} {'结果':>4s} {'正确':>4s}")
print("=" * 90)

odds_correct = 0
odds_probs = []
actuals = []
results = []

for home, away, hs, as_, oh, od, oa in matches:
    p_h, p_d, p_a = implied_prob(oh, od, oa)
    
    # 赔率预测（最高概率的结果）
    if p_h > p_d and p_h > p_a:
        pred = "H"
    elif p_a > p_d:
        pred = "A"
    else:
        pred = "D"
    
    # 实际结果
    if hs > as_:
        actual = "H"
    elif hs < as_:
        actual = "A"
    else:
        actual = "D"
    
    correct = pred == actual
    if correct:
        odds_correct += 1
    
    # Brier 计算
    a_h = 1.0 if actual == "H" else 0.0
    a_d = 1.0 if actual == "D" else 0.0
    a_a = 1.0 if actual == "A" else 0.0
    
    odds_probs.append((p_h, p_d, p_a))
    actuals.append((a_h, a_d, a_a))
    results.append(actual)
    
    mark = "✓" if correct else "✗"
    print(f"{home:>20s} vs {away:<20s} {hs}-{as_}  {pred:>4s}  {actual:>4s}  {mark}")

print("=" * 90)

# 统计
n = len(matches)
n_draws = sum(1 for r in results if r == "D")
n_homes = sum(1 for r in results if r == "H")
n_aways = sum(1 for r in results if r == "A")

# 赔率模型指标
odds_brier = brier_score(odds_probs, actuals)
odds_accuracy = odds_correct / n

# 赔率预测的平局命中率
draw_predictions = sum(1 for h, d, a in odds_probs if d > h and d > a)
draw_correct = sum(1 for i, (h, d, a) in enumerate(odds_probs) 
                   if d > h and d > a and results[i] == "D")
draw_recall = sum(1 for i, (h, d, a) in enumerate(odds_probs) 
                  if results[i] == "D" and d > h and d > a) / max(n_draws, 1)

print(f"\n{'='*60}")
print(f"赔率模型（市场隐含概率）预测结果")
print(f"{'='*60}")
print(f"总比赛数: {n}")
print(f"实际结果: 主胜={n_homes}, 平局={n_draws}, 客胜={n_aways}")
print(f"平局率: {n_draws/n*100:.1f}%")
print(f"")
print(f"赔率模型准确率: {odds_correct}/{n} = {odds_accuracy*100:.1f}%")
print(f"赔率模型 Brier Score: {odds_brier:.4f}")
print(f"赔率预测平局次数: {draw_predictions}")
print(f"赔率平局命中率 (Recall): {draw_recall*100:.1f}% ({draw_correct}/{n_draws})")

# 与 Elo+Poisson 对比
print(f"\n{'='*60}")
print(f"与 Elo+Poisson 基线对比")
print(f"{'='*60}")

# Elo+Poisson 基线（从之前的研究结果）
# Elo+Poisson 在本届世界杯的 Brier = 0.6536，准确率 = 40.9%
elo_brier = 0.6536
elo_accuracy = 0.409

print(f"Elo+Poisson 准确率: {elo_accuracy*100:.1f}%")
print(f"Elo+Poisson Brier: {elo_brier:.4f}")
print(f"")
print(f"赔率模型准确率: {odds_accuracy*100:.1f}%")
print(f"赔率模型 Brier: {odds_brier:.4f}")
print(f"")
print(f"准确率提升: {(odds_accuracy - elo_accuracy)*100:+.1f}%")
print(f"Brier 改善: {(elo_brier - odds_brier)/elo_brier*100:+.1f}%")

# 逐场分析赔率 vs 实际
print(f"\n{'='*60}")
print(f"逐场隐含概率分析")
print(f"{'='*60}")
print(f"{'比赛':<40s} {'P(H)':>5s} {'P(D)':>5s} {'P(A)':>5s} {'结果':>4s} {'最大概率':>6s}")
print("-" * 70)

for i, (home, away, hs, as_, oh, od, oa) in enumerate(matches):
    p_h, p_d, p_a = implied_prob(oh, od, oa)
    actual = results[i]
    max_prob = max(p_h, p_d, p_a)
    print(f"{home:>20s} vs {away:<17s} {p_h:.2f}  {p_d:.2f}  {p_a:.2f}  {actual:>4s}  {max_prob:.2f}")

# 平局分析
print(f"\n{'='*60}")
print(f"平局比赛赔率分析")
print(f"{'='*60}")
for i, (home, away, hs, as_, oh, od, oa) in enumerate(matches):
    if results[i] == "D":
        p_h, p_d, p_a = implied_prob(oh, od, oa)
        print(f"{home:>20s} {hs}-{as_} {away:<20s}  P(H)={p_h:.2f} P(D)={p_d:.2f} P(A)={p_a:.2f}")

# 频率基线
freq_h = n_homes / n
freq_d = n_draws / n
freq_a = n_aways / n
freq_brier = sum((freq_h - a_h)**2 + (freq_d - a_d)**2 + (freq_a - a_a)**2 
                 for a_h, a_d, a_a in actuals) / n
freq_correct = max(n_homes, n_draws, n_aways)

print(f"\n{'='*60}")
print(f"三模型对比总结")
print(f"{'='*60}")
print(f"{'模型':<25s} {'准确率':>8s} {'Brier':>8s} {'Draw命中率':>12s}")
print(f"{'-'*55}")
print(f"{'频率基线':<25s} {freq_correct/n*100:>7.1f}% {freq_brier:>8.4f} {'N/A':>12s}")
print(f"{'Elo+Poisson':<25s} {elo_accuracy*100:>7.1f}% {elo_brier:>8.4f} {'0.0%':>12s}")
print(f"{'赔率隐含概率':<25s} {odds_accuracy*100:>7.1f}% {odds_brier:>8.4f} {draw_recall*100:>11.1f}%")

# 保存结果
output = {
    "n_matches": n,
    "results": {"H": n_homes, "D": n_draws, "A": n_aways},
    "draw_rate": n_draws / n,
    "odds_model": {
        "accuracy": odds_accuracy,
        "brier": odds_brier,
        "draw_hit_rate": draw_recall,
        "correct": odds_correct,
    },
    "elo_poisson": {
        "accuracy": elo_accuracy,
        "brier": elo_brier,
        "draw_hit_rate": 0.0,
    },
    "frequency_baseline": {
        "accuracy": freq_correct / n,
        "brier": freq_brier,
    },
    "brier_improvement_vs_elo": (elo_brier - odds_brier) / elo_brier,
    "accuracy_improvement_vs_elo": odds_accuracy - elo_accuracy,
}

with open("/Users/liudapeng/Documents/code/others/world_cup/research/factor_demo/outputs/wc2026_odds_validation.json", "w") as f:
    json.dump(output, f, indent=2)

print(f"\n结果已保存到 outputs/wc2026_odds_validation.json")
