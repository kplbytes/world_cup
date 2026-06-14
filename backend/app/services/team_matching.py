"""Unified team name matching service.

Centralizes team code alias resolution and provider-specific name matching
to avoid scattered matching logic across providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team, TeamAlias


# FIFA code aliases: external code -> canonical team ID code
# These handle cases where different data sources use different 3-letter codes
# for the same country.
# Verified against data/seed/world-cup-2026.json canonical team IDs.
_CODE_ALIASES: dict[str, str] = {
    "URY": "URU",  # Uruguay: some sources use URY
    "HTI": "HAI",  # Haiti: some sources use HTI (our canonical is HAI)
    "IRI": "IRN",  # Iran: some sources use IRI
    "DZA": "ALG",  # Algeria: some sources use DZA
}


@dataclass(frozen=True)
class TeamMatchResult:
    """Result of a team name matching attempt."""
    team_id: str | None
    matched_name: str | None
    confidence: str  # "exact" | "alias" | "provider_alias" | "fuzzy" | "none"
    method: str  # description of how the match was found
    reason: str  # human-readable explanation


def resolve_code_alias(code: str) -> str:
    """Resolve a FIFA code alias to the canonical code.

    Examples:
        resolve_code_alias("URY") -> "URU"
        resolve_code_alias("URU") -> "URU"  (no change if already canonical)
    """
    return _CODE_ALIASES.get(code.upper(), code.upper())


def match_team(
    session: Session,
    raw_name: str,
    provider: str | None = None,
) -> TeamMatchResult:
    """Match a raw team name/string to an internal Team record.

    Matching priority:
    1. Exact team_id match (e.g., "BRA" -> Team with id="BRA")
    2. Code alias resolution (e.g., "URY" -> "URU" -> Team with id="URU")
    3. Provider alias match via TeamAlias table (e.g., "韩国" -> KOR for sporttery provider)
    4. No match

    Args:
        session: SQLAlchemy session
        raw_name: The raw team name or code from an external source
        provider: Optional provider name for alias lookup

    Returns:
        TeamMatchResult with match details
    """
    if not raw_name or not raw_name.strip():
        return TeamMatchResult(
            team_id=None, matched_name=None, confidence="none",
            method="empty_input", reason="Empty input string"
        )

    name = raw_name.strip()
    upper = name.upper()

    # 1. Exact team_id match
    team = session.get(Team, upper)
    if team:
        return TeamMatchResult(
            team_id=team.id, matched_name=team.name, confidence="exact",
            method="team_id_lookup", reason=f"Direct match on team_id={upper}"
        )

    # 2. Code alias resolution
    resolved = resolve_code_alias(upper)
    if resolved != upper:
        team = session.get(Team, resolved)
        if team:
            return TeamMatchResult(
                team_id=team.id, matched_name=team.name, confidence="alias",
                method="code_alias_resolution",
                reason=f"Alias {upper} -> {resolved} matched team"
            )

    # 3. Provider alias match
    alias_query = select(TeamAlias).where(TeamAlias.alias == name)
    if provider:
        alias_query = alias_query.where(TeamAlias.provider == provider)
    alias_row = session.scalar(alias_query.limit(1))
    if alias_row:
        team = session.get(Team, alias_row.team_id)
        if team:
            return TeamMatchResult(
                team_id=team.id, matched_name=team.name, confidence="provider_alias",
                method=f"provider_alias({alias_row.provider})",
                reason=f"Alias '{name}' from provider '{alias_row.provider}' -> {team.id}"
            )

    # 4. No match
    return TeamMatchResult(
        team_id=None, matched_name=None, confidence="none",
        method="no_match", reason=f"No match found for '{name}'"
    )


def match_team_id(session: Session, raw_name: str, provider: str | None = None) -> str | None:
    """Convenience function that returns just the team_id or None."""
    result = match_team(session, raw_name, provider)
    return result.team_id
