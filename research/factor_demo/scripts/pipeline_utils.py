"""流水线共享工具模块

提取自 run_pipeline.py / run_round2.py / run_round3.py / run_round4.py 的公共代码，
供各轮验证脚本和统一流水线复用。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 添加项目路径
PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from src.data.loader import load_international_results, filter_by_date, validate_data
from src.features.as_of import compute_all_features
from src.features.calculator import FACTOR_FUNCTIONS
from src.models.baseline import (
    HomeFixedBaseline,
    FrequencyBaseline,
    EloLogisticBaseline,
    EloPoissonBaseline,
    MarketImpliedBaseline,
)
from src.evaluation.metrics import evaluate_predictions, compare_models, EvaluationResult
from src.evaluation.calibration import reliability_diagram_data
from src.utils.elo_replay import replay_elo_history, EloConfig

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

# ============================================================
# 常量
# ============================================================

DATA_START = "2000-01-01"
TRAIN_START = "2010-01-01"
TRAIN_END = "2018-12-31"
VAL_START = "2019-01-01"
VAL_END = "2025-12-31"
FREEZE_DATE = "2025-12-31"

BASELINE_FACTORS = ["elo_diff"]
NEW_CANDIDATE_FACTORS = [
    "recent_form_5", "recent_form_10", "recent_form_5_opp_adjusted",
    "recent_goals_scored_5", "recent_goals_conceded_5", "recent_goal_diff_5",
    "attack_strength", "defense_strength", "official_vs_friendly",
    "home_away_neutral_form", "rest_days", "match_density_30d",
    "match_density_90d", "tournament_experience", "knockout_experience",
    "inter_confederation_form", "host_advantage", "h2h_last_5",
]
SKIP_FACTORS = ["fifa_rank_diff", "odds_implied_prob", "odds_movement"]
ALL_MODEL_FACTORS = BASELINE_FACTORS + NEW_CANDIDATE_FACTORS

RANDOM_SEED = 42
WORLD_CUP_YEARS = [2010, 2014, 2018, 2022]


# ============================================================
# 日志
# ============================================================

def setup_logging(output_dir: Path) -> logging.Logger:
    """设置日志，同时输出到控制台和文件。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(output_dir / "pipeline.log", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


# ============================================================
# 数据加载
# ============================================================

def load_and_prepare_data(
    data_path=None,
    start_date: str = "2010-01-01",
    end_date: str = "2025-12-31",
    exclude_2026: bool = True,
) -> pd.DataFrame:
    """通用数据加载流水线：加载 CSV、过滤日期、验证、排除加时赛。"""
    if data_path is not None:
        df = pd.read_csv(data_path)
    else:
        df = load_international_results()

    df = filter_by_date(df, DATA_START, end_date if not exclude_2026 else FREEZE_DATE)

    if exclude_2026:
        df = df[df["result"].notna()].copy()

    report = validate_data(df)
    print(f"  日期范围: {report['date_range']}")
    print(f"  球队数量: {report['unique_teams']}")
    print(f"  重复记录: {report['duplicates']}")

    df = filter_by_date(df, start_date, end_date if not exclude_2026 else FREEZE_DATE)
    return df


# ============================================================
# Elo + 特征计算
# ============================================================

def compute_elo_and_features(
    matches: pd.DataFrame,
    feature_funcs: dict | None = None,
    sample_size: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """通用 Elo 回放 + 特征计算流水线。

    Returns:
        (matches_with_elo, features_df)
    """
    if feature_funcs is None:
        feature_funcs = {k: v for k, v in FACTOR_FUNCTIONS.items() if k not in SKIP_FACTORS}

    print("  计算 Elo 历史...")
    matches = replay_elo_history(matches, EloConfig())

    target = matches
    if sample_size is not None and len(matches) > sample_size:
        target = matches.sample(sample_size, random_state=RANDOM_SEED)

    print(f"  计算特征 ({len(target)} 场比赛)...")
    features = compute_all_features(target, matches, feature_funcs, show_progress=True)

    return matches, features


# ============================================================
# 模型训练与评估
# ============================================================

def train_evaluate_model(
    model_class,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    **model_kwargs,
) -> dict:
    """训练模型并评估，返回指标字典。

    对于 sklearn 模型（如 LogisticRegression），model_class 为类本身。
    """
    model = model_class(**model_kwargs)
    model.fit(X_train, y_train)
    preds = model.predict_proba(X_test)
    ev = evaluate_predictions(preds, y_test)
    return eval_to_dict(ev)


def run_baseline_comparison(
    matches: pd.DataFrame,
    feature_df: pd.DataFrame,
    test_start: str = "2022-01-01",
) -> dict:
    """运行全部 5 个基线模型并返回对比结果。"""
    test = filter_by_date(matches, test_start, FREEZE_DATE)
    test_has_elo = test["pre_match_elo_home"].notna() & test["pre_match_elo_away"].notna()
    test_eval = test[test_has_elo]
    test_labels = test_eval["result"].values

    all_results = {}

    # 1. HomeFixed
    model = HomeFixedBaseline.from_data(
        filter_by_date(matches, TRAIN_START, "2018-12-31")
    )
    preds = []
    for _, m in test_eval.iterrows():
        p = model.predict()
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["HomeFixed"] = evaluate_predictions(np.array(preds), test_labels)

    # 2. Frequency
    freq_model = FrequencyBaseline().fit(
        filter_by_date(matches, TRAIN_START, "2018-12-31")
    )
    preds = []
    for _, m in test_eval.iterrows():
        p = freq_model.predict(m)
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["Frequency"] = evaluate_predictions(np.array(preds), test_labels)

    # 3. EloLogistic
    elo_model = EloLogisticBaseline()
    preds = []
    for _, m in test_eval.iterrows():
        p = elo_model.predict(
            elo_home=m["pre_match_elo_home"],
            elo_away=m["pre_match_elo_away"],
            is_neutral=m["is_neutral"],
        )
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["EloLogistic"] = evaluate_predictions(np.array(preds), test_labels)

    # 4. EloPoisson
    poisson_model = EloPoissonBaseline()
    preds = []
    for _, m in test_eval.iterrows():
        p = poisson_model.predict(
            elo_home=m["pre_match_elo_home"],
            elo_away=m["pre_match_elo_away"],
            is_neutral=m["is_neutral"],
        )
        preds.append([p.home_win, p.draw, p.away_win])
    all_results["EloPoisson"] = evaluate_predictions(np.array(preds), test_labels)

    # 5. MarketImplied（如有赔率数据）
    try:
        market_model = MarketImpliedBaseline()
        preds = []
        valid = 0
        for _, m in test_eval.iterrows():
            try:
                p = market_model.predict(m)
                preds.append([p.home_win, p.draw, p.away_win])
                valid += 1
            except Exception:
                pass
        if valid > 10:
            all_results["MarketImplied"] = evaluate_predictions(np.array(preds), test_labels[:valid])
    except Exception:
        pass

    return all_results


# ============================================================
# Bootstrap 对比
# ============================================================

def run_bootstrap_comparison(
    predictions_a: np.ndarray,
    labels: np.ndarray,
    predictions_b: np.ndarray,
    n_bootstrap: int = 5000,
) -> dict:
    """运行两组预测之间的 Bootstrap 对比。

    比较 predictions_b 相对于 predictions_a 的 Brier 差异。
    正值表示 b 优于 a。
    """
    rng = np.random.RandomState(RANDOM_SEED)
    label_map = {"H": 0, "D": 1, "A": 2}
    n = len(labels)
    y_true_onehot = np.zeros((n, 3))
    for i, label in enumerate(labels):
        y_true_onehot[i, label_map[label]] = 1.0

    a_briers = np.sum((predictions_a - y_true_onehot) ** 2, axis=1)
    b_briers = np.sum((predictions_b - y_true_onehot) ** 2, axis=1)
    diffs = a_briers - b_briers  # positive = b better

    boot_diffs = []
    for _ in range(n_bootstrap):
        idx = rng.randint(0, n, size=n)
        boot_diffs.append(np.mean(diffs[idx]))
    boot_diffs = np.array(boot_diffs)

    mean_diff = float(np.mean(boot_diffs))
    ci_low = float(np.percentile(boot_diffs, 2.5))
    ci_high = float(np.percentile(boot_diffs, 97.5))
    significant = not (ci_low <= 0 <= ci_high)

    return {
        "mean_brier_diff": mean_diff,
        "ci_95_low": ci_low,
        "ci_95_high": ci_high,
        "significant": significant,
        "n_bootstrap": n_bootstrap,
        "direction": "better" if mean_diff > 0 else "worse",
    }


# ============================================================
# 结果格式化与保存
# ============================================================

def format_results_table(results: dict) -> str:
    """将模型对比结果格式化为 Markdown 表格。"""
    lines = []
    lines.append("| 模型 | Brier | LogLoss | Accuracy | ECE | N |")
    lines.append("|------|-------|---------|----------|-----|---|")
    for name, r in sorted(results.items(), key=lambda x: _get_brier(x[1])):
        brier = _get_brier(r)
        logloss = _get_metric(r, "log_loss")
        acc = _get_metric(r, "accuracy")
        ece = _get_metric(r, "ece")
        n = _get_metric(r, "n_samples")
        acc_str = f"{acc:.1%}" if isinstance(acc, float) else str(acc)
        lines.append(f"| {name} | {brier:.4f} | {logloss:.4f} | {acc_str} | {ece:.4f} | {n} |")
    return "\n".join(lines)


def save_results(results: dict, output_dir: Path, filename: str):
    """保存结果到 JSON，处理 numpy 类型和 EvaluationResult 对象。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename

    serializable = _make_serializable(results)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, default=_json_default)
    print(f"  已保存: {path}")


def generate_promotion_decision(
    factor_results: dict,
    baseline_brier: float,
    threshold: float = 0.02,
) -> dict:
    """根据准则生成因子准入决策。

    Args:
        factor_results: 逐因子分析结果，需包含 coverage_rate, ablation_brier_delta, is_redundant 等
        baseline_brier: 基线 Brier 分数
        threshold: 相对改善阈值（默认 2%）
    """
    decisions = {}
    for name, info in factor_results.items():
        coverage = info.get("coverage_rate", 0)
        ablation_delta = info.get("ablation_brier_delta")
        is_redundant = info.get("is_redundant", False)

        if is_redundant:
            decision = "rejected"
            reason = "与 elo_diff 高度冗余 (|r| > 0.7)"
        elif coverage < 0.3:
            decision = "needs_more_data"
            reason = f"覆盖率不足 ({coverage:.1%})"
        elif ablation_delta is not None and ablation_delta > 0.001:
            decision = "candidate"
            reason = f"消融显示正向贡献 (Δ={ablation_delta:+.4f})"
        elif ablation_delta is not None and ablation_delta < -0.001:
            decision = "rejected"
            reason = f"消融显示负向贡献 (Δ={ablation_delta:+.4f})"
        else:
            decision = "needs_more_data"
            reason = f"贡献不显著 (Δ={ablation_delta:+.4f})" if ablation_delta is not None else "无消融数据"

        decisions[name] = {"decision": decision, "reason": reason}

    return decisions


def print_section(title: str):
    """打印格式化的章节标题。"""
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ============================================================
# 特征合并与列名映射
# ============================================================

def merge_features(df_slice: pd.DataFrame, feature_df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """将特征 DataFrame 合并回比赛数据。

    合并后冲突列（如 elo_diff）会有 _match 和 _feat 后缀，
    非冲突列保持原名。返回 (merged_df, feature_col_names)。
    """
    merged = df_slice.merge(feature_df, on="match_id", how="left", suffixes=("_match", "_feat"))

    for c in feature_df.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        match_col = f"{c}_match"
        if feat_col in merged.columns and match_col in merged.columns:
            if merged[feat_col].isna().all() and not merged[match_col].isna().all():
                merged[feat_col] = merged[match_col]

    feature_cols = []
    for c in feature_df.columns:
        if c == "match_id":
            continue
        feat_col = f"{c}_feat"
        if feat_col in merged.columns:
            feature_cols.append(feat_col)
        elif c in merged.columns:
            feature_cols.append(c)
    return merged, feature_cols


def get_factor_cols(merged: pd.DataFrame, factor_names: list[str]) -> list[str]:
    """将逻辑因子名映射到合并后 DataFrame 中的实际列名。"""
    cols = []
    for fn in factor_names:
        feat_col = f"{fn}_feat"
        if feat_col in merged.columns:
            cols.append(feat_col)
        elif fn in merged.columns:
            cols.append(fn)
    return cols


# ============================================================
# 模型工具
# ============================================================

def predict_elo_baseline(model, df_slice: pd.DataFrame) -> tuple[np.ndarray, list]:
    """用 Elo 基线模型预测一批比赛，跳过无 Elo 数据的比赛。"""
    preds = []
    valid_indices = []
    for idx, m in df_slice.iterrows():
        elo_h = m.get("pre_match_elo_home", None)
        elo_a = m.get("pre_match_elo_away", None)
        if elo_h is None or elo_a is None or pd.isna(elo_h) or pd.isna(elo_a):
            continue
        p = model.predict(elo_home=elo_h, elo_away=elo_a, is_neutral=m.get("is_neutral", False))
        preds.append([p.home_win, p.draw, p.away_win])
        valid_indices.append(idx)
    return np.array(preds), valid_indices


def train_lr_model(X_train: np.ndarray, y_train: np.ndarray, C: float = 1.0):
    """训练逻辑回归模型，返回 (model, scaler)。"""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train)
    lr = LogisticRegression(max_iter=2000, C=C, solver="lbfgs")
    lr.fit(X_scaled, y_train)
    return lr, scaler


def predict_lr(lr, scaler, X: np.ndarray) -> np.ndarray:
    """用逻辑回归模型预测概率。"""
    X_scaled = scaler.transform(X)
    return lr.predict_proba(X_scaled)


# ============================================================
# 内部工具
# ============================================================

def eval_to_dict(ev) -> dict:
    """将 EvaluationResult 转为可序列化字典。"""
    return {
        "brier_score": ev.brier_score,
        "brier_home": ev.brier_home,
        "brier_draw": ev.brier_draw,
        "brier_away": ev.brier_away,
        "log_loss": ev.log_loss,
        "accuracy": ev.accuracy,
        "n_samples": ev.n_samples,
        "home_win_accuracy": ev.home_win_accuracy,
        "draw_accuracy": ev.draw_accuracy,
        "away_win_accuracy": ev.away_win_accuracy,
        "ece": ev.ece,
    }


def _json_default(obj):
    """JSON 序列化回退：处理 numpy 类型和 pandas Timestamp。"""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, EvaluationResult):
        return eval_to_dict(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _make_serializable(obj):
    """递归地将对象转为可 JSON 序列化的结构。"""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, EvaluationResult):
        return eval_to_dict(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    return obj


def _get_brier(r):
    """从结果字典或 EvaluationResult 中提取 brier_score。"""
    if isinstance(r, EvaluationResult):
        return r.brier_score
    if isinstance(r, dict):
        val = r.get("validation", r)
        if isinstance(val, dict):
            return val.get("brier_score", 1.0)
        if isinstance(val, EvaluationResult):
            return val.brier_score
    return 1.0


def _get_metric(r, key: str):
    """从结果字典或 EvaluationResult 中提取指定指标。"""
    if isinstance(r, EvaluationResult):
        return getattr(r, key, None)
    if isinstance(r, dict):
        val = r.get("validation", r)
        if isinstance(val, dict):
            return val.get(key, None)
        if isinstance(val, EvaluationResult):
            return getattr(val, key, None)
    return None
