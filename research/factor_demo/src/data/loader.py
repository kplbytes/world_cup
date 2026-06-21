"""数据加载与标准化模块

从 CSV 文件加载历史比赛数据，完成标准化和清洗。
所有数据必须保留 source, fetched_at, effective_at, available_before_kickoff 字段。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


DATA_DIR = Path(__file__).parent.parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# 数据版本常量：用于 fetched_at 字段，确保可复现性
DATA_VERSION = "2025-12-31"

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
    df["fetched_at"] = DATA_VERSION
    df["effective_at"] = df["match_date"].dt.strftime("%Y-%m-%d")
    df["available_before_kickoff"] = True

    # 生成唯一比赛ID
    df["match_id"] = df.apply(_generate_match_id, axis=1)

    # 洲际归属
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


# 洲际归属映射（覆盖 Kaggle 国际足球比赛数据集中出现的所有国家队）
_CONFEDERATION_MAP: dict[str, str] = {
    # ── UEFA ──
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
    "Andorra": "UEFA", "Faroe Islands": "UEFA", "Luxembourg": "UEFA",
    "Malta": "UEFA", "Liechtenstein": "UEFA", "San Marino": "UEFA",
    "Gibraltar": "UEFA", "Kosovo": "UEFA", "Armenia": "UEFA",
    "Azerbaijan": "UEFA", "Kazakhstan": "UEFA", "Cyprus": "UEFA",
    "Moldova": "UEFA", "Belarus": "UEFA", "Lithuania": "UEFA",
    "Latvia": "UEFA", "Estonia": "UEFA", "Serbia and Montenegro": "UEFA",
    "Yugoslavia": "UEFA", "West Germany": "UEFA", "East Germany": "UEFA",
    "Saarland": "UEFA",
    # ── CONMEBOL ──
    "Brazil": "CONMEBOL", "Argentina": "CONMEBOL", "Uruguay": "CONMEBOL",
    "Colombia": "CONMEBOL", "Chile": "CONMEBOL", "Peru": "CONMEBOL",
    "Ecuador": "CONMEBOL", "Paraguay": "CONMEBOL", "Bolivia": "CONMEBOL",
    "Venezuela": "CONMEBOL",
    # ── CONCACAF ──
    "United States": "CONCACAF", "Mexico": "CONCACAF", "Canada": "CONCACAF",
    "Costa Rica": "CONCACAF", "Jamaica": "CONCACAF", "Honduras": "CONCACAF",
    "Panama": "CONCACAF", "Trinidad and Tobago": "CONCACAF", "El Salvador": "CONCACAF",
    "Guatemala": "CONCACAF", "Haiti": "CONCACAF", "Cuba": "CONCACAF",
    "Anguilla": "CONCACAF", "Antigua and Barbuda": "CONCACAF",
    "Aruba": "CONCACAF", "Bahamas": "CONCACAF", "Barbados": "CONCACAF",
    "Belize": "CONCACAF", "Bermuda": "CONCACAF", "British Virgin Islands": "CONCACAF",
    "Cayman Islands": "CONCACAF", "Dominica": "CONCACAF",
    "Dominican Republic": "CONCACAF", "Grenada": "CONCACAF",
    "Guyana": "CONCACAF", "Montserrat": "CONCACAF", "Puerto Rico": "CONCACAF",
    "Saint Kitts and Nevis": "CONCACAF", "Saint Lucia": "CONCACAF",
    "Saint Vincent and the Grenadines": "CONCACAF", "Sint Maarten": "CONCACAF",
    "Suriname": "CONCACAF", "Turks and Caicos Islands": "CONCACAF",
    "US Virgin Islands": "CONCACAF", "Bonaire": "CONCACAF",
    "Nicaragua": "CONCACAF", "Martinique": "CONCACAF", "Guadeloupe": "CONCACAF",
    "Curaçao": "CONCACAF",
    # ── CAF ──
    "Nigeria": "CAF", "Cameroon": "CAF", "Ghana": "CAF", "Senegal": "CAF",
    "Egypt": "CAF", "Algeria": "CAF", "Tunisia": "CAF", "Morocco": "CAF",
    "South Africa": "CAF", "Ivory Coast": "CAF", "Côte d'Ivoire": "CAF",
    "DR Congo": "CAF", "Congo": "CAF", "Mali": "CAF", "Zambia": "CAF",
    "Kenya": "CAF", "Ethiopia": "CAF", "Tanzania": "CAF", "Uganda": "CAF",
    "Guinea": "CAF", "Burkina Faso": "CAF", "Libya": "CAF", "Angola": "CAF",
    "Sudan": "CAF", "Niger": "CAF", "Togo": "CAF", "Benin": "CAF",
    "Sierra Leone": "CAF", "Liberia": "CAF", "Rwanda": "CAF", "Burundi": "CAF",
    "Equatorial Guinea": "CAF", "Gabon": "CAF", "Central African Republic": "CAF",
    "Chad": "CAF", "Comoros": "CAF", "Djibouti": "CAF", "Eritrea": "CAF",
    "Gambia": "CAF", "Guinea-Bissau": "CAF", "Lesotho": "CAF",
    "Madagascar": "CAF", "Malawi": "CAF", "Mauritania": "CAF",
    "Mauritius": "CAF", "Mozambique": "CAF", "Namibia": "CAF",
    "Sao Tome and Principe": "CAF", "Seychelles": "CAF", "Somalia": "CAF",
    "South Sudan": "CAF", "Eswatini": "CAF", "Swaziland": "CAF",
    "Zimbabwe": "CAF", "Congo DR": "CAF", "Zaire": "CAF",
    "Rhodesia": "CAF", "Cape Verde": "CAF",
    # ── AFC ──
    "Japan": "AFC", "South Korea": "AFC", "Iran": "AFC", "Saudi Arabia": "AFC",
    "Australia": "AFC", "China": "AFC", "Qatar": "AFC", "Iraq": "AFC",
    "Uzbekistan": "AFC", "United Arab Emirates": "AFC", "Oman": "AFC",
    "Syria": "AFC", "Jordan": "AFC", "Bahrain": "AFC", "Kuwait": "AFC",
    "Thailand": "AFC", "Vietnam": "AFC", "Lebanon": "AFC", "Palestine": "AFC",
    "India": "AFC", "North Korea": "AFC", "Korea Republic": "AFC",
    "Korea DPR": "AFC", "Afghanistan": "AFC", "Bangladesh": "AFC",
    "Bhutan": "AFC", "Brunei": "AFC", "Cambodia": "AFC",
    "Chinese Taipei": "AFC", "Guam": "AFC", "Kyrgyzstan": "AFC",
    "Laos": "AFC", "Macau": "AFC", "Maldives": "AFC", "Mongolia": "AFC",
    "Myanmar": "AFC", "Nepal": "AFC", "Northern Mariana Islands": "AFC",
    "Pakistan": "AFC", "Philippines": "AFC", "Singapore": "AFC",
    "Sri Lanka": "AFC", "Tajikistan": "AFC", "Timor-Leste": "AFC",
    "Turkmenistan": "AFC", "Yemen": "AFC", "Taiwan": "AFC",
    # ── OFC ──
    "New Zealand": "OFC", "Fiji": "OFC", "Papua New Guinea": "OFC",
    "Solomon Islands": "OFC", "Vanuatu": "OFC", "Samoa": "OFC",
    "Tonga": "OFC", "Cook Islands": "OFC", "American Samoa": "OFC",
    "New Caledonia": "OFC", "Tahiti": "OFC",
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


# ──────────────────────────────────────────────────────────────────────
# FIFA 排名数据
# ──────────────────────────────────────────────────────────────────────

# FIFA 排名数据集与 Kaggle 比赛结果数据集之间的队名映射
_FIFA_NAME_MAP: dict[str, str] = {
    "Korea Republic": "South Korea",
    "Korea DPR": "North Korea",
    "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast",
    "IR Iran": "Iran",
    "China PR": "China",
    "Chinese Taipei": "Chinese Taipei",
    "USA": "United States",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "North Macedonia": "North Macedonia",
    "Cabo Verde": "Cape Verde",
    "São Tomé and Príncipe": "Sao Tome and Principe",
    "Eswatini": "Eswatini",
    "Türkiye": "Turkey",
    "Curacao": "Curaçao",
    "St. Kitts and Nevis": "Saint Kitts and Nevis",
    "St. Lucia": "Saint Lucia",
    "St. Vincent / Grenadines": "Saint Vincent and the Grenadines",
    "US Virgin Islands": "US Virgin Islands",
    "Congo DR": "DR Congo",
}


def _map_fifa_team_name(name: str) -> str:
    """将 FIFA 排名数据集中的队名映射为 Kaggle 比赛结果数据集中的队名。"""
    return _FIFA_NAME_MAP.get(name, name)


def load_fifa_rankings(csv_path: Path | None = None) -> pd.DataFrame:
    """加载 FIFA 排名数据集。

    数据来源: https://www.kaggle.com/datasets/cashncarry/fifaworldranking

    原始列: rank, country_full, country_abrv, total_points, previous_points, rank_date
    标准化后: team_name, fifa_rank, fifa_points, rank_date
    """
    if csv_path is None:
        csv_path = Path(__file__).parent.parent.parent.parent.parent / "data" / "external" / "fifa_ranking.csv"

    df = pd.read_csv(csv_path)

    # 标准化列名
    df = df.rename(columns={
        "rank": "fifa_rank",
        "country_full": "team_name",
        "total_points": "fifa_points",
    })

    # 保留需要的列
    available_cols = [c for c in ["team_name", "fifa_rank", "fifa_points", "rank_date"] if c in df.columns]
    df = df[available_cols]

    # 日期标准化
    df["rank_date"] = pd.to_datetime(df["rank_date"], utc=True)

    # 队名映射：统一为 Kaggle 比赛结果数据集的队名
    df["team_name"] = df["team_name"].apply(_map_fifa_team_name)

    # 按日期排序
    df = df.sort_values("rank_date").reset_index(drop=True)

    return df


def inject_fifa_rankings(matches: pd.DataFrame, rankings: pd.DataFrame) -> pd.DataFrame:
    """将 FIFA 排名注入比赛数据。

    对每场比赛，查找比赛日期之前最近一次的 FIFA 排名，为主客队分别添加排名信息。
    使用 as-of 逻辑：仅使用比赛日期之前的排名数据。

    新增列:
        - pre_match_fifa_rank_home: 主队赛前 FIFA 排名
        - pre_match_fifa_rank_away: 客队赛前 FIFA 排名
        - fifa_rank_diff: 主客队排名差（home - away，负值表示主队排名更高）
    """
    matches = matches.copy()

    # 确保日期列类型一致
    match_dates = matches["match_date"].dt.tz_localize(None) if matches["match_date"].dt.tz is not None else matches["match_date"]
    rank_dates = rankings["rank_date"].dt.tz_localize(None) if rankings["rank_date"].dt.tz is not None else rankings["rank_date"]

    # 获取所有排名日期
    rank_date_values = rank_dates.sort_values().unique()

    home_ranks = []
    away_ranks = []

    for _, row in matches.iterrows():
        match_dt = row["match_date"]
        if hasattr(match_dt, "tz") and match_dt.tz is not None:
            match_dt = match_dt.tz_localize(None)

        # 找到比赛日期之前最近的排名日期
        prior_dates = rank_date_values[rank_date_values <= match_dt]
        if len(prior_dates) == 0:
            home_ranks.append(pd.NA)
            away_ranks.append(pd.NA)
            continue

        latest_rank_date = prior_dates[-1]

        # 获取该排名日期的排名数据
        rank_snapshot = rankings[rank_dates == latest_rank_date]

        # 主队排名
        home_rank_row = rank_snapshot[rank_snapshot["team_name"] == row["home_team"]]
        home_rank = home_rank_row["fifa_rank"].values[0] if len(home_rank_row) > 0 else pd.NA

        # 客队排名
        away_rank_row = rank_snapshot[rank_snapshot["team_name"] == row["away_team"]]
        away_rank = away_rank_row["fifa_rank"].values[0] if len(away_rank_row) > 0 else pd.NA

        home_ranks.append(home_rank)
        away_ranks.append(away_rank)

    matches["pre_match_fifa_rank_home"] = home_ranks
    matches["pre_match_fifa_rank_away"] = away_ranks

    # 排名差：负值表示主队排名更高（数字越小排名越高）
    matches["fifa_rank_diff"] = pd.to_numeric(matches["pre_match_fifa_rank_home"], errors="coerce") - \
                                pd.to_numeric(matches["pre_match_fifa_rank_away"], errors="coerce")

    return matches


# ──────────────────────────────────────────────────────────────────────
# 赔率数据
# ──────────────────────────────────────────────────────────────────────

def load_odds_data(csv_path: Path | None = None) -> pd.DataFrame:
    """加载赔率数据集。

    期望列: match_date, home_team, away_team, odds_home, odds_draw, odds_away
    返回标准化后的 DataFrame。
    """
    if csv_path is None:
        csv_path = Path(__file__).parent.parent.parent.parent.parent / "data" / "external" / "odds_data.csv"

    df = pd.read_csv(csv_path)

    # 日期标准化
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True)

    return df


def inject_odds(matches: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    """将赔率数据注入比赛数据。

    通过日期 + 主客队匹配赔率数据，计算隐含概率（去除利润率）。

    新增列:
        - odds_home: 主胜赔率
        - odds_draw: 平局赔率
        - odds_away: 客胜赔率
        - odds_implied_home: 主胜隐含概率
        - odds_implied_draw: 平局隐含概率
        - odds_implied_away: 客胜隐含概率
        - odds_implied_home_advantage: 主胜隐含概率 - 客胜隐含概率
    """
    matches = matches.copy()

    # 准备赔率数据的合并键：统一日期格式
    odds_merge = odds.copy()
    odds_merge["_merge_date"] = odds_merge["match_date"].dt.date

    matches_merge = matches.copy()
    matches_merge["_merge_date"] = matches_merge["match_date"].dt.date

    # 左连接：保留所有比赛，无赔率数据的为 NaN
    merged = matches_merge.merge(
        odds_merge[["_merge_date", "home_team", "away_team", "odds_home", "odds_draw", "odds_away"]],
        on=["_merge_date", "home_team", "away_team"],
        how="left",
    )

    # 计算隐含概率（去除利润率 / overround）
    odds_h = pd.to_numeric(merged["odds_home"], errors="coerce")
    odds_d = pd.to_numeric(merged["odds_draw"], errors="coerce")
    odds_a = pd.to_numeric(merged["odds_away"], errors="coerce")

    raw_implied_h = 1.0 / odds_h
    raw_implied_d = 1.0 / odds_d
    raw_implied_a = 1.0 / odds_a

    overround = raw_implied_h + raw_implied_d + raw_implied_a

    merged["odds_implied_home"] = raw_implied_h / overround
    merged["odds_implied_draw"] = raw_implied_d / overround
    merged["odds_implied_away"] = raw_implied_a / overround
    merged["odds_implied_home_advantage"] = merged["odds_implied_home"] - merged["odds_implied_away"]

    # 将赔率列写回 matches
    odds_cols = [
        "odds_home", "odds_draw", "odds_away",
        "odds_implied_home", "odds_implied_draw", "odds_implied_away",
        "odds_implied_home_advantage",
    ]
    for col in odds_cols:
        matches[col] = merged[col].values

    return matches


# ──────────────────────────────────────────────────────────────────────
# 加时赛 / 点球排除
# ──────────────────────────────────────────────────────────────────────

def exclude_extra_time_matches(df: pd.DataFrame) -> pd.DataFrame:
    """排除进入加时赛或点球大战的比赛（仅保留 90 分钟结果）。

    如果数据包含 home_penalty / away_penalty / home_extra_time / away_extra_time 等列，
    则过滤掉这些比赛。如果不存在这些列（如当前 Kaggle 数据集），则原样返回并记录警告日志，
    因为该数据集的比分通常已代表 90 分钟结果。
    """
    penalty_cols = [c for c in ["home_penalty", "away_penalty"] if c in df.columns]
    extra_time_cols = [c for c in ["home_extra_time", "away_extra_time"] if c in df.columns]

    if not penalty_cols and not extra_time_cols:
        logger.warning(
            "数据集中未找到加时赛/点球相关列（home_penalty, away_penalty, "
            "home_extra_time, away_extra_time），无法过滤加时赛比赛。"
            "Kaggle 数据集的比分通常已代表 90 分钟结果，此操作为空操作。"
        )
        return df

    mask = pd.Series(True, index=df.index)

    # 排除有点球比分的比赛
    for col in penalty_cols:
        mask = mask & df[col].isna()

    # 排除有加时赛比分的比赛
    for col in extra_time_cols:
        mask = mask & df[col].isna()

    excluded_count = (~mask).sum()
    if excluded_count > 0:
        logger.info(f"排除了 {excluded_count} 场加时赛/点球比赛")

    return df[mask].copy()
