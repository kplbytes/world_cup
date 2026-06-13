from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSnapshot, Match, SyncRun, TeamRating
from app.prediction.elo import update_elo
from app.providers.sporttery import SportteryUnavailable
from app.services.market import fetch_and_store_market_data
from app.services.recompute import recompute_all
from app.services.scoring import save_model_score, score_model, snapshot_prediction


@dataclass(frozen=True)
class RefreshOutcome:
    status: str
    finalized_matches: int = 0
    updated_matches: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    revision_id: int | None = None


def refresh_tournament(
    session: Session,
    providers: list,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> RefreshOutcome:
    sync_run = SyncRun(status="running")
    session.add(sync_run)
    session.flush()
    errors: list[str] = []
    warnings: list[str] = []
    successful_payloads = []
    for provider in providers:
        try:
            successful_payloads.append(provider.load())
        except Exception as exc:
            errors.append(str(exc))

    if not successful_payloads:
        sync_run.status = "failed"
        sync_run.finished_at = datetime.now(timezone.utc)
        sync_run.errors = errors
        session.flush()
        return RefreshOutcome(status="failed", errors=errors)

    finalized = 0
    updated = 0
    for payload in successful_payloads:
        payload_json = payload.model_dump_json()
        checksum = sha256(payload_json.encode()).hexdigest()
        existing_snapshot = session.scalar(
            select(DataSnapshot).where(
                DataSnapshot.provider == payload.source.provider,
                DataSnapshot.checksum == checksum,
            )
        )
        if existing_snapshot is None:
            session.add(
                DataSnapshot(
                    provider=payload.source.provider,
                    source_url=payload.source.source_url,
                    fetched_at=payload.source.fetched_at,
                    status="available",
                    checksum=checksum,
                    coverage={"teams": len(payload.teams), "matches": len(payload.matches)},
                )
            )
        for incoming in payload.matches:
            stored = session.get(Match, incoming.id)
            if stored is None:
                warnings.append(f"unknown canonical match ignored: {incoming.id}")
                continue
            if stored.status == "final":
                if incoming.status == "final" and (
                    stored.home_score != incoming.home_score
                    or stored.away_score != incoming.away_score
                ):
                    warnings.append(f"conflicting final score ignored: {incoming.id}")
                continue
            if incoming.status == "final":
                stored.status = "final"
                stored.home_score = incoming.home_score
                stored.away_score = incoming.away_score
                stored.source = payload.source.provider
                stored.source_updated_at = payload.source.fetched_at
                snapshot_prediction(session, stored.id)
                _update_final_ratings(session, stored)
                finalized += 1
                updated += 1
            elif not _same_instant(incoming.kickoff, stored.kickoff) or incoming.venue != stored.venue:
                stored.kickoff = incoming.kickoff
                stored.venue = incoming.venue
                stored.source_updated_at = payload.source.fetched_at
                updated += 1

    # Fetch market odds (non-blocking: failures logged as warnings)
    try:
        market_count = fetch_and_store_market_data(session)
        if market_count:
            warnings.append(f"sporttery: {market_count} matches matched")
    except SportteryUnavailable as exc:
        warnings.append(f"sporttery unavailable: {exc}")
    except Exception as exc:
        warnings.append(f"sporttery error: {exc}")

    revision_id = None
    if updated:
        revision = recompute_all(session, iterations=iterations, seed=seed)
        revision_id = revision.id
        report = score_model(session)
        save_model_score(session, report, revision.id)

    sync_run.status = "success"
    sync_run.finished_at = datetime.now(timezone.utc)
    sync_run.updated_count = updated
    sync_run.finalized_matches = finalized
    sync_run.warnings = warnings
    sync_run.errors = errors
    session.flush()
    return RefreshOutcome(
        status="success",
        finalized_matches=finalized,
        updated_matches=updated,
        warnings=warnings,
        errors=errors,
        revision_id=revision_id,
    )


def _update_final_ratings(session: Session, match: Match) -> None:
    if match.home_score is None or match.away_score is None:
        raise ValueError("final result requires both scores")
    home = _latest_rating(session, match.home_team_id)
    away = _latest_rating(session, match.away_team_id)
    result = update_elo(home.elo, away.elo, match.home_score, match.away_score, weight=40.0)
    effective_date = match.kickoff.date() if match.kickoff else date.today()
    session.add_all(
        [
            TeamRating(
                team_id=match.home_team_id,
                effective_date=effective_date,
                fifa_rank=home.fifa_rank,
                fifa_points=home.fifa_points,
                elo=result.home,
                recent_form=home.recent_form,
                source=f"result:{match.id}",
            ),
            TeamRating(
                team_id=match.away_team_id,
                effective_date=effective_date,
                fifa_rank=away.fifa_rank,
                fifa_points=away.fifa_points,
                elo=result.away,
                recent_form=away.recent_form,
                source=f"result:{match.id}",
            ),
        ]
    )
    session.flush()


def _latest_rating(session: Session, team_id: str) -> TeamRating:
    rating = session.scalar(
        select(TeamRating)
        .where(TeamRating.team_id == team_id)
        .order_by(TeamRating.effective_date.desc(), TeamRating.id.desc())
        .limit(1)
    )
    if rating is None:
        raise ValueError(f"missing rating for {team_id}")
    return rating


def _same_instant(left: datetime, right: datetime) -> bool:
    return _utc_naive(left) == _utc_naive(right)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
