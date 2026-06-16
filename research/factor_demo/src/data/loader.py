"""数据加载与标准化模块

从 CSV 文件加载历史比赛数据，完成标准化和清洗。
所有数据必须保留 source, fetched_at, effective_at, available_before_kickoff 字段。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


DATA_DIR = Path(__file__).parent.parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# 赛事类型权重（正式比赛权重1.0，友谊赛降权）
TOURNAMENT_WEIGHTS: dict[str, float] = {
    "friendly": 0.5,
    "qualification": 0.9,
    "continental": 0.95,
    "world_cup": 1.0,
    "nations_league": 0.85,
}

# 赛事分类映射
TOURNAMENT_CATEGORIES: dict[str, str] = {
    "Friendly": "friendly",
    "FIFA World Cup": "world_cup",
    "FIFA World Cup Qualification": "qualification",
    "UEFA Euro": "continental",
    "UEFA Euro Qualification": "qualification",
    "Copa América": "continental",
    "African Cup of Nations": "continental",
    "African Cup of Nations Qualification": "qualification",
    "AFC Asian Cup": "continental",
    "AFC Asian Cup Qualification": "qualification",
    "CONCACAF Gold Cup": "continental",
    "CONCACAF Gold Cup Qualification": "qualification",
    "OFC Nations Cup": "continental",
    "UEFA Nations League": "nations_league",
    "CONCACAF Nations League": "nations_league",
}


def load_international_results(csv_path: Path | None = None) -> pd.DataFrame:
    """加载 Kaggle International Football Results 数据集。
    
    数据来源: https://www.kaggle.com/datasets/martj42/international-football-results-from-1872-to-2017
    """
    if csv_path is None:
        csv_path = Path(__file__).parent.parent.parent.parent.parent / "data" / "external" / "international_results.csv"
    
    df = pd.read_csv(csv_path)
    df = _standardize_international_results(df)
    return df


def _standardize_international_results(df: pd.DataFrame) -> pd.DataFrame:
    """标准化 international_results.csv 数据。"""
    # 统一列名
    df = df.rename(columns={
        "date": "match_date",
        "home_team": "home_team",
        "away_team": "away_team",
        "home_score": "home_goals",
        "away_score": "away_goals",
    })
    
    # 日期标准化为 UTC
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
    df["kickoff_utc"] = df["match_date"]  # 数据集无具体开球时间，用日期代替
    
    # 比赛结果
    df["result"] = df.apply(
        lambda r: "H" if r["home_goals"] > r["away_goals"]
        else ("A" if r["home_goals"] < r["away_goals"] else "D"),
        axis=1,
    )
    
    # 中立场标识
    df["is_neutral"] = df["neutral"].astype(bool)
    
    # 赛事分类
    df["tournament_category"] = df["tournament"].apply(_categorize_tournament)
    
    # 赛事权重
    df["match_weight"] = df["tournament_category"].map(TOURNAMENT_WEIGHTS).fillna(0.7)
    
    # 是否为正式比赛
    df["is_official"] = df["tournament_category"] != "friendly"
    
    # 数据溯源字段
    df["source"] = "kaggle_international_results"
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    df["effective_at"] = df["match_date"].dt.strftime("%Y-%m-%d")
    df["available_before_kickoff"] = True
    
    # 生成唯一比赛ID
    df["match_id"] = df.apply(_generate_match_id, axis=1)
    
    # 洲际归属（简化版，基于常见国家队）
    df["home_confederation"] = df["home_team"].map(_get_confederation)
    df["away_confederation"] = df["away_team"].map(_get_confederation)
    df["is_cross_confederation"] = df["home_confederation"] != df["away_confederation"]
    
    return df


def _categorize_tournament(tournament: str) -> str:
    """将赛事名称映射到赛事类别。"""
    tournament_lower = tournament.lower()
    
    # 精确匹配
    if tournament in TOURNAMENT_CATEGORIES:
        return TOURNAMENT_CATEGORIES[tournament]
    
    # 模糊匹配
    if "world cup" in tournament_lower and "qualif" not in tournament_lower:
        return "world_cup"
    if "world cup" in tournament_lower and "qualif" in tournament_lower:
        return "qualification"
    if "friendly" in tournament_lower:
        return "friendly"
    if "euro" in tournament_lower and "qualif" not in tournament_lower:
        return "continental"
    if "euro" in tournament_lower and "qualif" in tournament_lower:
        return "qualification"
    if "copa" in tournament_lower:
        return "continental"
    if "african" in tournament_lower or "afcon" in tournament_lower:
        if "qualif" in tournament_lower:
            return "qualification"
        return "continental"
    if "asian cup" in tournament_lower:
        if "qualif" in tournament_lower:
            return "qualification"
        return "continental"
    if "gold cup" in tournament_lower:
        if "qualif" in tournament_lower:
            return "qualification"
        return "continental"
    if "nations league" in tournament_lower:
        return "nations_league"
    if "qualif" in tournament_lower:
        return "qualification"
    
    return "other"


def _generate_match_id(row: pd.Series) -> str:
    """生成唯一比赛ID。"""
    key = f"{row['match_date']}_{row['home_team']}_{row['away_team']}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


# 简化版洲际归属映射（覆盖主要国家队）
_CONFEDERATION_MAP: dict[str, str] = {
    # UEFA
    "Germany": "UEFA", "France": "UEFA", "Spain": "UEFA", "Italy": "UEFA",
    "England": "UEFA", "Netherlands": "UEFA", "Portugal": "UEFA", "Belgium": "UEFA",
    "Croatia": "UEFA", "Serbia": "UEFA", "Switzerland": "UEFA", "Austria": "UEFA",
    "Denmark": "UEFA", "Sweden": "UEFA", "Poland": "UEFA", "Ukraine": "UEFA",
    "Czech Republic": "UEFA", "Czechoslovakia": "UEFA", "Romania": "UEFA",
    "Turkey": "UEFA", "Scotland": "UEFA", "Wales": "UEFA", "Ireland": "UEFA",
    "Republic of Ireland": "UEFA", "Norway": "UEFA", "Finland": "UEFA",
    "Slovakia": "UEFA", "Hungary": "UEFA", "Greece": "UEFA", "Russia": "UEFA",
    "Soviet Union": "UEFA", "Bosnia and Herzegovina": "UEFA", "Iceland": "UEFA",
    "North Macedonia": "UEFA", "Montenegro": "UEFA", "Slovenia": "UEFA",
    "Albania": "UEFA", "Bulgaria": "UEFA", "Israel": "UEFA", "Georgia": "UEFA",
    # CONMEBOL
    "Brazil": "CONMEBOL", "Argentina": "CONMEBOL", "Uruguay": "CONMEBOL",
    "Colombia": "CONMEBOL", "Chile": "CONMEBOL", "Peru": "CONMEBOL",
    "Ecuador": "CONMEBOL", "Paraguay": "CONMEBOL", "Bolivia": "CONMEBOL",
    "Venezuela": "CONMEBOL",
    # CONCACAF
    "United States": "CONCACAF", "Mexico": "CONCACAF", "Canada": "CONCACAF",
    "Costa Rica": "CONCACAF", "Jamaica": "CONCACAF", "Honduras": "CONCACAF",
    "Panama": "CONCACAF", "Trinidad and Tobago": "CONCACAF", "El Salvador": "CONCACAF",
    "Guatemala": "CONCACAF", "Haiti": "CONCACAF", "Cuba": "CONCACAF",
    # CAF
    "Nigeria": "CAF", "Cameroon": "CAF", "Ghana": "CAF", "Senegal": "CAF",
    "Egypt": "CAF", "Algeria": "CAF", "Tunisia": "CAF", "Morocco": "CAF",
    "South Africa": "CAF", "Ivory Coast": "CAF", "Côte d'Ivoire": "CAF",
    "DR Congo": "CAF", "Congo": "CAF", "Mali": "CAF", "Zambia": "CAF",
    "Kenya": "CAF", "Ethiopia": "CAF", "Tanzania": "CAF", "Uganda": "CAF",
    "Guinea": "CAF", "Burkina Faso": "CAF", "Libya": "CAF", "Angola": "CAF",
    # AFC
    "Japan": "AFC", "South Korea": "AFC", "Iran": "AFC", "Saudi Arabia": "AFC",
    "Australia": "AFC", "China": "AFC", "Qatar": "AFC", "Iraq": "AFC",
    "Uzbekistan": "AFC", "United Arab Emirates": "AFC", "Oman": "AFC",
    "Syria": "AFC", "Jordan": "AFC", "Bahrain": "AFC", "Kuwait": "AFC",
    "Thailand": "AFC", "Vietnam": "AFC", "Lebanon": "AFC", "Palestine": "AFC",
    "India": "AFC", "North Korea": "AFC", "Korea Republic": "AFC",
    "Korea DPR": "AFC",
    # OFC
    "New Zealand": "OFC", "Fiji": "OFC", "Papua New Guinea": "OFC",
    "Solomon Islands": "OFC", "Vanuatu": "OFC", "Samoa": "OFC",
    "Tonga": "OFC", "Cook Islands": "OFC",
}


def _get_confederation(team: str) -> str:
    """获取国家队所属洲际足联。"""
    return _CONFEDERATION_MAP.get(team, "Unknown")


def validate_data(df: pd.DataFrame) -> dict:
    """验证数据质量，返回验证报告。"""
    report = {
        "total_matches": len(df),
        "date_range": (str(df["match_date"].min()), str(df["match_date"].max())),
        "unique_teams": len(set(df["home_team"].unique()) | set(df["away_team"].unique())),
        "duplicates": 0,
        "invalid_scores": 0,
        "missing_dates": 0,
        "confederation_coverage": 0.0,
        "issues": [],
    }
    
    # 检查重复
    dup_mask = df.duplicated(subset=["match_date", "home_team", "away_team"], keep=False)
    report["duplicates"] = int(dup_mask.sum())
    if report["duplicates"] > 0:
        report["issues"].append(f"发现 {report['duplicates']} 条重复比赛记录")
    
    # 检查比分合法性
    invalid_scores = df[(df["home_goals"] < 0) | (df["away_goals"] < 0)]
    report["invalid_scores"] = len(invalid_scores)
    if report["invalid_scores"] > 0:
        report["issues"].append(f"发现 {report['invalid_scores']} 条非法比分")
    
    # 检查缺失日期
    report["missing_dates"] = int(df["match_date"].isna().sum())
    
    # 洲际覆盖率
    all_teams = set(df["home_team"].unique()) | set(df["away_team"].unique())
    mapped = sum(1 for t in all_teams if _get_confederation(t) != "Unknown")
    report["confederation_coverage"] = mapped / len(all_teams) if all_teams else 0.0
    
    return report


def filter_by_date(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """按日期范围过滤比赛数据。"""
    start_dt = pd.Timestamp(start, tz="UTC")
    end_dt = pd.Timestamp(end, tz="UTC")
    mask = (df["match_date"] >= start_dt) & (df["match_date"] <= end_dt)
    return df[mask].copy()
