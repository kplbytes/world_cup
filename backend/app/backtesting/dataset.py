"""Versioned dataset for backtesting with strict time boundaries."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


DATASET_VERSION = "international-history-v1"

# Fixed time splits
TRAIN_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
TRAIN_END = datetime(2022, 1, 1, tzinfo=timezone.utc)  # exclusive
VAL_START = datetime(2022, 1, 1, tzinfo=timezone.utc)
VAL_END = datetime(2024, 1, 1, tzinfo=timezone.utc)  # exclusive
TEST_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
TEST_END = datetime(2026, 6, 11, tzinfo=timezone.utc)  # before WC kickoff

# WC 2026 kickoff date - no WC results allowed in parameter selection
WC_2026_START = datetime(2026, 6, 11, tzinfo=timezone.utc)


@dataclass
class DatasetSplit:
    """A fixed data split for backtesting."""
    name: str
    start: datetime  # available_at >= start
    end: datetime    # available_at < end
    match_ids: list[str] = field(default_factory=list)
    match_count: int = 0
    competition_types: dict[str, int] = field(default_factory=dict)
    team_count: int = 0


@dataclass
class VersionedDataset:
    """A versioned dataset with train/val/test splits."""
    version: str
    created_at: datetime
    train: DatasetSplit
    validation: DatasetSplit
    test: DatasetSplit
    total_matches: int = 0
    excluded_wc_2026: int = 0


def build_dataset(session) -> VersionedDataset:
    """Build the versioned dataset from historical matches.

    Rules:
    1. Only matches with available_at in the split range
    2. Only matches with score_scope == "full_90min"
    3. Only matches where is_unmapped == False
    4. date_only matches follow next-day visibility rule (already handled by available_at)
    5. WC 2026 results are excluded from all splits
    """
    from sqlalchemy import select, func
    from app.models import HistoricalMatch

    # Build each split
    train = _build_split("train", TRAIN_START, TRAIN_END, session)
    val = _build_split("validation", VAL_START, VAL_END, session)
    test = _build_split("test", TEST_START, TEST_END, session)

    # Count WC 2026 matches (excluded)
    wc_2026_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(
            HistoricalMatch.available_at >= WC_2026_START,
            HistoricalMatch.is_unmapped.is_(False),
            HistoricalMatch.score_scope == "full_90min",
        )
    )

    return VersionedDataset(
        version=DATASET_VERSION,
        created_at=datetime.now(timezone.utc),
        train=train,
        validation=val,
        test=test,
        total_matches=train.match_count + val.match_count + test.match_count,
        excluded_wc_2026=wc_2026_count or 0,
    )


def _build_split(name: str, start: datetime, end: datetime, session) -> DatasetSplit:
    from sqlalchemy import select, func
    from app.models import HistoricalMatch

    # Get matches for this split
    matches = list(session.scalars(
        select(HistoricalMatch)
        .where(
            HistoricalMatch.available_at >= start,
            HistoricalMatch.available_at < end,
            HistoricalMatch.is_unmapped.is_(False),
            HistoricalMatch.score_scope == "full_90min",
        )
        .order_by(HistoricalMatch.available_at)
    ))

    match_ids = [m.source_match_id for m in matches]

    # Count by competition type
    comp_types: dict[str, int] = {}
    for m in matches:
        ct = m.competition_type or "other"
        comp_types[ct] = comp_types.get(ct, 0) + 1

    # Count unique teams
    teams = set()
    for m in matches:
        if m.home_team_id:
            teams.add(m.home_team_id)
        if m.away_team_id:
            teams.add(m.away_team_id)

    return DatasetSplit(
        name=name,
        start=start,
        end=end,
        match_ids=match_ids,
        match_count=len(matches),
        competition_types=comp_types,
        team_count=len(teams),
    )
