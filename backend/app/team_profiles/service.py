from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.models import Match, Team, TeamProfile, TeamProfileMatchHistory, TeamRating
from app.team_profiles import PROFILE_VERSION
from app.team_profiles.data_loader import load_profile_match_history_snapshot, seed_combined_history
from app.team_profiles.feature_engineering import generate_traits, rate, tier_statistics


DISPLAY_ONLY_SCOPE = "display_only"
ROOT = Path(__file__).resolve().parents[3]
VENUE_REGISTRY_PATH = ROOT / "data" / "seed" / "world-cup-2026-venues.json"
VENUE_CLIMATE_PATH = ROOT / "data" / "seed" / "world-cup-2026-venue-climate.json"
STATSBOMB_XG_PATH = ROOT / "data" / "external" / "statsbomb" / "world_cup_xg.json"
FIFA_SQUAD_PATH = ROOT / "data" / "seed" / "world-cup-2026-squads.json"
STATSBOMB_XG_SOURCE_TAG = "statsbomb_xg:open_data_world_cup:2018_2022"
FIFA_SQUAD_SOURCE_TAG = "fifa_squad:fifa_official_squad_list:2026-06-20"
STATSBOMB_TEAM_ALIASES = {
    "Bosnia and Herzegovina": "Bosnia & Herzegovina",
    "Cabo Verde": "Cabo Verde",
    "Cape Verde": "Cabo Verde",
    "Congo DR": "Congo DR",
    "Côte d'Ivoire": "Cote d'Ivoire",
    "Cote d'Ivoire": "Cote d'Ivoire",
    "Curacao": "Curaçao",
    "Curaçao": "Curaçao",
    "Iran": "IR Iran",
    "IR Iran": "IR Iran",
    "Korea Republic": "Korea Republic",
    "South Korea": "Korea Republic",
    "Türkiye": "Türkiye",
    "Turkey": "Türkiye",
    "USA": "United States",
    "United States": "United States",
}
CORE_SCORE_FIELDS = (
    "long_term_strength_score",
    "recent_form_score",
    "attack_score",
    "defense_score",
    "stability_score",
    "tournament_experience_score",
)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _pct(value: float) -> int:
    return round(_clamp(value * 100))


def _record(rows: list[TeamProfileMatchHistory]) -> dict:
    return {
        "sample_count": len(rows),
        "wins": sum(1 for row in rows if row.result == "win"),
        "draws": sum(1 for row in rows if row.result == "draw"),
        "losses": sum(1 for row in rows if row.result == "loss"),
        "goals_for": sum(row.goals_for for row in rows),
        "goals_against": sum(row.goals_against for row in rows),
        "goal_difference": sum(row.goals_for - row.goals_against for row in rows),
        "win_rate": rate(rows, lambda row: row.result == "win"),
        "not_loss_rate": rate(rows, lambda row: row.result != "loss"),
    }


def _level(score: float) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _statsbomb_team_key(value: str) -> str:
    canonical = STATSBOMB_TEAM_ALIASES.get(value, value)
    return re.sub(r"[^a-z0-9]+", "", canonical.lower())


@lru_cache(maxsize=1)
def _statsbomb_xg_index() -> dict[str, dict]:
    if not STATSBOMB_XG_PATH.exists():
        return {}
    data = json.loads(STATSBOMB_XG_PATH.read_text(encoding="utf-8"))
    aggregate: dict[str, dict] = {}
    competitions = set()
    seasons = set()
    for match in data.values():
        competitions.add(match.get("competition"))
        seasons.add(str(match.get("season")))
        if match.get("competition") != "World Cup" or str(match.get("season")) not in {"2018", "2022"}:
            continue
        home = _statsbomb_team_key(match["home_team"])
        away = _statsbomb_team_key(match["away_team"])
        for key in (home, away):
            aggregate.setdefault(key, {"matches": 0, "xg_for": 0.0, "xg_against": 0.0})
        aggregate[home]["matches"] += 1
        aggregate[home]["xg_for"] += float(match["home_xg"])
        aggregate[home]["xg_against"] += float(match["away_xg"])
        aggregate[away]["matches"] += 1
        aggregate[away]["xg_for"] += float(match["away_xg"])
        aggregate[away]["xg_against"] += float(match["home_xg"])
    if competitions != {"World Cup"} or seasons != {"2018", "2022"}:
        return {}
    return {
        key: {
            "source": "statsbomb_open_data",
            "competition": "World Cup",
            "seasons": ["2018", "2022"],
            "sample_count": value["matches"],
            "xg_for_avg": round(value["xg_for"] / value["matches"], 3),
            "xg_against_avg": round(value["xg_against"] / value["matches"], 3),
        }
        for key, value in aggregate.items()
        if value["matches"]
    }


def _statsbomb_xg_for_team(team: Team) -> dict | None:
    index = _statsbomb_xg_index()
    return index.get(_statsbomb_team_key(team.name)) or index.get(_statsbomb_team_key(team.short_name or team.name))


@lru_cache(maxsize=1)
def _fifa_squad_index() -> dict[str, dict]:
    if not FIFA_SQUAD_PATH.exists():
        return {}
    payload = json.loads(FIFA_SQUAD_PATH.read_text(encoding="utf-8"))
    teams = payload.get("teams") or {}
    return {
        code: {
            **team.get("summary", {}),
            "coach": team.get("coach"),
            "source": payload.get("source", {}),
        }
        for code, team in teams.items()
    }


def _fifa_squad_for_team(team: Team) -> dict | None:
    return _fifa_squad_index().get(team.code)


def _strength_grade(score: float) -> str:
    if score >= 82:
        return "A"
    if score >= 68:
        return "B"
    if score >= 52:
        return "C"
    return "D"


def _recent_form_score(rows: list[TeamProfileMatchHistory]) -> float:
    recent = rows[-10:]
    if not recent:
        return 0.0
    total_weight = 0.0
    score = 0.0
    tier_weight = {"elite": 1.35, "strong": 1.18, "mid": 1.0, "weak": 0.82}
    competition_weight = {"world_cup": 1.25, "qualifier": 1.12}
    for idx, row in enumerate(recent):
        result_value = 1.0 if row.result == "win" else 0.5 if row.result == "draw" else 0.0
        recency_weight = 0.72 + (idx + 1) / len(recent) * 0.28
        match_type_weight = competition_weight.get("world_cup" if row.is_world_cup else "qualifier" if row.is_qualifier else "other", 0.65)
        weight = recency_weight * match_type_weight
        adjusted = result_value * tier_weight.get(row.opponent_tier, 1.0)
        if row.is_home:
            adjusted *= 0.96
        score += min(1.0, adjusted) * weight
        total_weight += weight
    return _clamp(score / total_weight * 100 if total_weight else 0.0)


def _build_tactical_tags(metrics: dict, traits: list[str]) -> list[str]:
    tags = []
    if metrics["goal_for_avg"] >= 2.1 and metrics["goal_against_avg"] <= 0.9:
        tags.append("强压制型")
    if metrics["goal_for_avg"] <= 1.25 and metrics["goal_against_avg"] <= 0.9:
        tags.append("保守低比分型")
    if metrics["goal_for_avg"] >= 1.8 and metrics["goal_against_avg"] >= 1.1:
        tags.append("开放对攻型")
    if metrics["under_2_5_rate"] >= 0.65:
        tags.append("小比分倾向")
    if metrics["over_2_5_rate"] >= 0.58:
        tags.append("大比分倾向")
    if metrics["opening_match_slow_start_score"] >= 0.65:
        tags.append("慢热型")
    if "防守优先" in traits:
        tags.append("防守反击型")
    if not tags:
        tags.append("均衡型")
    return list(dict.fromkeys(tags))


def _traceable_source_list(sources: set[str], source_summary: dict, team_rating: TeamRating | None) -> list[str]:
    source_list = []
    for source in sorted(sources):
        if source == "historical_real":
            provider = source_summary.get("provider", "unknown_provider")
            digest = source_summary.get("raw_sha256")
            suffix = f":{digest[:12]}" if digest else ""
            source_list.append(f"historical_real:{provider}{suffix}")
        elif source.startswith("seed_mock"):
            source_list.append(f"{source}:local_seed_fallback")
        else:
            source_list.append(source)
    if team_rating and team_rating.source != "test":
        rating_date = team_rating.effective_date.isoformat()
        source_list.append(f"elo:{team_rating.source}:{rating_date}")
        if team_rating.fifa_rank is not None:
            source_list.append(f"fifa_ranking:{team_rating.source}:{rating_date}")
    return source_list


def _data_quality_score(*, contains_mock: bool, competitive_count: int, missing_fields: list[str], source_list: list[str]) -> tuple[float, dict[str, float]]:
    penalties: dict[str, float] = {}
    if contains_mock:
        penalties["contains_mock"] = 60.0
    if competitive_count < 20:
        penalties["small_competitive_sample"] = 15.0
    if any(field in missing_fields for field in ("lineup_integrity_score", "injury_risk_score", "confirmed_lineup_level")):
        penalties["lineup_player_unavailable"] = 12.0
    if any(field in missing_fields for field in ("rest_days", "travel_distance", "timezone_shift")):
        penalties["schedule_environment_unavailable"] = 6.0
    if any(field in missing_fields for field in ("climate_adaptation", "venue_familiarity", "heat_humidity_altitude")):
        penalties["climate_venue_unavailable"] = 4.0
    if "xg" in missing_fields:
        penalties["xg_unavailable"] = 5.0
    if any(field in missing_fields for field in ("shots", "shots_on_target_rate")):
        penalties["shot_volume_unavailable"] = 3.0
    if "fifa_rank" in missing_fields:
        penalties["fifa_rank_missing"] = 3.0
    if missing_fields:
        penalties["other_missing_fields"] = min(8.0, len(missing_fields) * 0.3)
    if not source_list:
        penalties["source_list_missing"] = 20.0
    return _clamp(95.0 - sum(penalties.values())), penalties


def _fatigue_score(rest_days: float | None) -> float | None:
    if rest_days is None:
        return None
    if rest_days >= 6:
        return 90.0
    if rest_days >= 5:
        return 80.0
    if rest_days >= 4:
        return 70.0
    if rest_days >= 3:
        return 55.0
    return 35.0


@lru_cache(maxsize=1)
def _venue_registry() -> dict:
    if not VENUE_REGISTRY_PATH.exists():
        return {"source": {}, "venues": {}}
    return json.loads(VENUE_REGISTRY_PATH.read_text(encoding="utf-8"))


def _venue_metadata(venue: str | None) -> dict | None:
    if not venue:
        return None
    metadata = (_venue_registry().get("venues") or {}).get(venue)
    if not metadata:
        return None
    return {"venue": venue, **metadata}


def _distance_km(a: dict | None, b: dict | None) -> float | None:
    if not a or not b:
        return None
    lat1 = math.radians(float(a["latitude"]))
    lon1 = math.radians(float(a["longitude"]))
    lat2 = math.radians(float(b["latitude"]))
    lon2 = math.radians(float(b["longitude"]))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return round(6371.0 * 2 * math.asin(math.sqrt(h)), 1)


def _timezone_shift_hours(previous_venue: dict | None, next_venue: dict | None, previous_kickoff: datetime | None, next_kickoff: datetime | None) -> float | None:
    if not previous_venue or not next_venue or previous_kickoff is None or next_kickoff is None:
        return None
    previous_offset = previous_kickoff.astimezone(ZoneInfo(previous_venue["timezone"])).utcoffset()
    next_offset = next_kickoff.astimezone(ZoneInfo(next_venue["timezone"])).utcoffset()
    if previous_offset is None or next_offset is None:
        return None
    return round((next_offset - previous_offset).total_seconds() / 3600.0, 1)


def _travel_score(distance_km: float | None) -> float | None:
    if distance_km is None:
        return None
    if distance_km >= 3500:
        return 45.0
    if distance_km >= 2200:
        return 60.0
    if distance_km >= 1000:
        return 75.0
    return 90.0


def _timezone_score(shift_hours: float | None) -> float | None:
    if shift_hours is None:
        return None
    shift = abs(shift_hours)
    if shift >= 3:
        return 55.0
    if shift >= 2:
        return 70.0
    if shift >= 1:
        return 85.0
    return 95.0


def _environment_score(rest_days: float | None, travel_distance_km: float | None, timezone_shift_hours: float | None) -> float | None:
    components = []
    fatigue = _fatigue_score(rest_days)
    if fatigue is not None:
        components.append((fatigue, 0.5))
    travel = _travel_score(travel_distance_km)
    if travel is not None:
        components.append((travel, 0.3))
    timezone_component = _timezone_score(timezone_shift_hours)
    if timezone_component is not None:
        components.append((timezone_component, 0.2))
    total_weight = sum(weight for _, weight in components)
    if not total_weight:
        return None
    return round(sum(score * weight for score, weight in components) / total_weight, 1)


@lru_cache(maxsize=1)
def _venue_climate() -> dict:
    if not VENUE_CLIMATE_PATH.exists():
        return {"source": {}, "venues": {}}
    return json.loads(VENUE_CLIMATE_PATH.read_text(encoding="utf-8"))


def _climate_score(baseline: dict | None) -> float | None:
    if not baseline:
        return None
    score = 100.0
    temp = baseline.get("temperature_2m_mean_c")
    humidity = baseline.get("relative_humidity_2m_mean_pct")
    rain_rate = baseline.get("rain_day_rate")
    wind = baseline.get("wind_speed_10m_max_mean_kmh")
    if temp is not None:
        if temp >= 30 or temp <= 5:
            score -= 25
        elif temp >= 26 or temp <= 10:
            score -= 12
    if humidity is not None:
        if humidity >= 80:
            score -= 12
        elif humidity >= 70:
            score -= 6
    if rain_rate is not None:
        if rain_rate >= 0.45:
            score -= 10
        elif rain_rate >= 0.25:
            score -= 5
    if wind is not None and wind >= 28:
        score -= 5
    return round(_clamp(score), 1)


def _climate_context(venue: str | None, kickoff: datetime | None) -> dict | str:
    if not venue or kickoff is None:
        return "unavailable"
    payload = _venue_climate()
    venue_payload = (payload.get("venues") or {}).get(venue)
    if not venue_payload:
        return "unavailable"
    month_key = str(kickoff.month)
    baseline = (venue_payload.get("baseline_by_month") or {}).get(month_key)
    if not baseline:
        return "unavailable"
    return {
        "type": "historical_climate_baseline",
        "source": (payload.get("source") or {}).get("provider"),
        "source_url": (payload.get("source") or {}).get("source_url"),
        "years": (payload.get("source") or {}).get("years"),
        "month": kickoff.month,
        "is_match_forecast": False,
        "baseline": baseline,
        "climate_comfort_score": _climate_score(baseline),
    }


def _combined_environment_score(
    rest_days: float | None,
    travel_distance_km: float | None,
    timezone_shift_hours: float | None,
    climate_adaptation: dict | str,
) -> float | None:
    base = _environment_score(rest_days, travel_distance_km, timezone_shift_hours)
    climate = climate_adaptation.get("climate_comfort_score") if isinstance(climate_adaptation, dict) else None
    if base is None:
        return climate
    if climate is None:
        return base
    return round(base * 0.82 + climate * 0.18, 1)


def _schedule_context(session: Session, team_id: str, cutoff: datetime) -> dict:
    matches = list(session.scalars(
        select(Match)
        .where(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))
        .order_by(Match.kickoff)
    ))
    normalized = []
    for match in matches:
        kickoff = _as_utc(match.kickoff)
        normalized.append((match, kickoff))
    previous = next((item for item in reversed(normalized) if item[1] <= cutoff), None)
    upcoming = [item for item in normalized if item[1] > cutoff]
    next_item = upcoming[0] if upcoming else None
    rest_days = None
    if previous and next_item:
        rest_days = round((next_item[1] - previous[1]).total_seconds() / 86400.0, 1)
    upcoming_venues = list(dict.fromkeys(match.venue for match, _ in upcoming[:3] if match.venue))
    previous_venue = _venue_metadata(previous[0].venue) if previous else None
    next_venue = _venue_metadata(next_item[0].venue) if next_item else None
    travel_distance_km = _distance_km(previous_venue, next_venue)
    timezone_shift_hours = _timezone_shift_hours(
        previous_venue,
        next_venue,
        previous[1] if previous else None,
        next_item[1] if next_item else None,
    )
    climate_adaptation = _climate_context(next_item[0].venue if next_item else None, next_item[1] if next_item else None)
    environment_adaptation_score = _combined_environment_score(rest_days, travel_distance_km, timezone_shift_hours, climate_adaptation)
    return {
        "rest_days": rest_days,
        "schedule_fatigue_score": _fatigue_score(rest_days),
        "travel_distance_km": travel_distance_km,
        "timezone_shift_hours": timezone_shift_hours,
        "environment_adaptation_score": environment_adaptation_score,
        "climate_adaptation": climate_adaptation,
        "previous_venue": previous_venue,
        "next_venue": next_venue,
        "venue_registry_source": _venue_registry().get("source", {}),
        "upcoming_venues": upcoming_venues,
        "next_match": {
            "match_id": next_item[0].id,
            "kickoff": next_item[1].isoformat(),
            "venue": next_item[0].venue,
            "source": next_item[0].source,
        } if next_item else None,
        "source_list": sorted({match.source for match, _ in normalized if match.source and match.source != "test"}),
    }


def _build_structured_profile(
    *,
    rows: list[TeamProfileMatchHistory],
    competitive: list[TeamProfileMatchHistory],
    recent: list[TeamProfileMatchHistory],
    world_cup: list[TeamProfileMatchHistory],
    knockout: list[TeamProfileMatchHistory],
    qualifier: list[TeamProfileMatchHistory],
    strong_rows: list[TeamProfileMatchHistory],
    weak_rows: list[TeamProfileMatchHistory],
    tier_stats: dict,
    metrics: dict,
    traits: list[str],
    sources: set[str],
    source_summary: dict,
    team_rating: TeamRating | None,
    schedule_context: dict,
    statsbomb_xg: dict | None,
    fifa_squad: dict | None,
    cutoff: datetime,
) -> dict:
    rest_days = schedule_context.get("rest_days")
    schedule_fatigue_score = schedule_context.get("schedule_fatigue_score")
    travel_distance_km = schedule_context.get("travel_distance_km")
    timezone_shift_hours = schedule_context.get("timezone_shift_hours")
    environment_adaptation_score = schedule_context.get("environment_adaptation_score")
    climate_adaptation = schedule_context.get("climate_adaptation", "unavailable")
    contains_mock = any(source.startswith("seed_mock") for source in sources)
    verified_xg = None if contains_mock else statsbomb_xg
    verified_squad = None if contains_mock else fifa_squad
    missing_fields = [
        "fifa_rank" if not team_rating or team_rating.fifa_rank is None else None,
        "xg" if verified_xg is None else None,
        "shots",
        "shots_on_target_rate",
        "core_player_dependency" if verified_squad is None else None,
        "set_piece_strength",
        "shots_against",
        "counterattack_vulnerability",
        "late_goal_conceded_rate",
        "lineup_integrity_score",
        "injury_risk_score",
        "captain_status",
        "top_scorer_status" if verified_squad is None else None,
        "starting_goalkeeper_status",
        "yellow_card_suspension_risk",
        "confirmed_lineup_level",
        "bench_depth" if verified_squad is None else None,
        "rest_days" if rest_days is None else None,
        "travel_distance" if travel_distance_km is None else None,
        "timezone_shift" if timezone_shift_hours is None else None,
        "climate_adaptation" if climate_adaptation == "unavailable" else None,
        "venue_familiarity",
        "consecutive_away_matches",
        "heat_humidity_altitude",
    ]
    missing_fields = [item for item in missing_fields if item]
    source_list = _traceable_source_list(sources, source_summary, team_rating)
    for source in schedule_context.get("source_list", []):
        source_list.append(f"schedule:{source}")
    if schedule_context.get("venue_registry_source") and (schedule_context.get("previous_venue") or schedule_context.get("next_venue")):
        source_list.append(f"venue_registry:{schedule_context['venue_registry_source'].get('provider', 'unknown')}")
    if isinstance(climate_adaptation, dict) and climate_adaptation.get("source"):
        source_list.append(f"climate:{climate_adaptation['source']}")
    if verified_xg:
        source_list.append(STATSBOMB_XG_SOURCE_TAG)
    if verified_squad:
        source_list.append(FIFA_SQUAD_SOURCE_TAG)
    source_list = list(dict.fromkeys(source_list))

    official_recent_5 = competitive[-5:]
    official_recent_10 = competitive[-10:]
    recent_5_record = _record(official_recent_5)
    recent_10_record = _record(official_recent_10)
    recent_form_score = _recent_form_score(competitive)
    recent_goal_for_avg = sum(row.goals_for for row in official_recent_5) / len(official_recent_5) if official_recent_5 else 0.0
    recent_goal_against_avg = sum(row.goals_against for row in official_recent_5) / len(official_recent_5) if official_recent_5 else 0.0
    recent_unbeaten_streak = 0
    for row in reversed(competitive):
        if row.result == "loss":
            break
        recent_unbeaten_streak += 1
    state_declining = len(official_recent_10) >= 8 and recent_5_record["not_loss_rate"] + 0.2 < recent_10_record["not_loss_rate"]

    elo_score = _clamp(((team_rating.elo if team_rating else 1500.0) - 1300.0) / 900.0 * 100.0)
    fifa_score = _clamp(100.0 - ((team_rating.fifa_rank - 1) / 80.0 * 100.0)) if team_rating and team_rating.fifa_rank else None
    record = _record(competitive)
    gd_per_match = record["goal_difference"] / len(competitive) if competitive else 0.0
    long_term_strength_score = _clamp(
        elo_score * 0.45
        + (fifa_score if fifa_score is not None else elo_score) * 0.08
        + record["not_loss_rate"] * 100 * 0.22
        + _clamp((gd_per_match + 1.5) / 3.0 * 100.0) * 0.15
        + metrics["world_cup_experience_score"] * 100 * 0.10
    )
    attack_score = _clamp(metrics["goal_for_avg"] / 2.6 * 70 + (1.0 - metrics["failed_to_score_rate"]) * 30)
    defense_score = _clamp((1.0 - min(metrics["goal_against_avg"], 2.2) / 2.2) * 65 + metrics["clean_sheet_rate"] * 35)
    stability_score = _clamp(metrics["recent_tournament_consistency"] * 45 + record["not_loss_rate"] * 35 + (1.0 - metrics["draw_rate_overall"]) * 20)
    tournament_experience_score = _clamp(metrics["world_cup_experience_score"] * 45 + metrics["knockout_experience_score"] * 35 + rate(qualifier, lambda row: row.result != "loss") * 20)
    tactical_style_tags = _build_tactical_tags(metrics, traits)

    strengths = []
    weaknesses = []
    risk_flags = []
    if attack_score >= 70:
        strengths.append("进攻产出稳定")
    elif attack_score < 45:
        weaknesses.append("进攻产出偏弱")
    if defense_score >= 70:
        strengths.append("防守稳定性较高")
    elif defense_score < 45:
        weaknesses.append("防守波动偏大")
    if tournament_experience_score >= 70:
        strengths.append("大赛经验充足")
    if recent_form_score < 45:
        risk_flags.append("recent_form_low")
    if contains_mock:
        risk_flags.append("mock_data_present")
    if state_declining:
        risk_flags.append("recent_form_declining")

    core_player_dependency = None
    bench_depth = "unavailable"
    top_scorer_status = "unavailable"
    if verified_squad:
        squad_goals = verified_squad.get("total_goals") or 0
        top_scorers = verified_squad.get("top_scorers_in_squad") or []
        top_goals = top_scorers[0].get("goals", 0) if top_scorers else 0
        core_player_dependency = round(top_goals / squad_goals, 3) if squad_goals else None
        position_counts = verified_squad.get("position_counts") or {}
        depth_components = [
            100.0 if position_counts.get("GK", 0) >= 3 else 60.0,
            100.0 if position_counts.get("DF", 0) >= 7 else 70.0,
            100.0 if position_counts.get("MF", 0) >= 6 else 70.0,
            100.0 if position_counts.get("FW", 0) >= 5 else 70.0,
        ]
        bench_depth = {
            "squad_size": verified_squad.get("squad_size"),
            "position_counts": position_counts,
            "depth_score": round(sum(depth_components) / len(depth_components), 1),
        }
        top_scorer_status = "in_official_squad" if top_scorers else "unavailable"

    data_quality_score, quality_penalties = _data_quality_score(
        contains_mock=contains_mock,
        competitive_count=len(competitive),
        missing_fields=missing_fields,
        source_list=source_list,
    )
    quality_label = "high" if data_quality_score >= 75 else "medium" if data_quality_score >= 55 else "low"

    opponent_performance = {
        "strong": {**_record(strong_rows), **tier_stats["elite"], "combined_sample_count": len(strong_rows)},
        "middle": tier_stats["mid"],
        "weak": {**_record(weak_rows), **tier_stats["weak"], "combined_sample_count": len(weak_rows)},
    }
    modules = {
        "long_term_strength": {
            "score": round(long_term_strength_score, 1),
            "grade": _strength_grade(long_term_strength_score),
            "elo": team_rating.elo if team_rating else None,
            "fifa_rank": team_rating.fifa_rank if team_rating else None,
            "fifa_points": team_rating.fifa_points if team_rating else None,
            "rating_source": team_rating.source if team_rating else None,
            "rating_effective_date": team_rating.effective_date.isoformat() if team_rating else None,
            "two_year_record": record,
            "opponent_performance": opponent_performance,
            "tournament_experience_score": round(tournament_experience_score, 1),
        },
        "recent_form": {
            "score": round(recent_form_score, 1),
            "recent_5": recent_5_record,
            "recent_10": recent_10_record,
            "recent_5_goal_for_avg": recent_goal_for_avg,
            "recent_5_goal_against_avg": recent_goal_against_avg,
            "unbeaten_streak": recent_unbeaten_streak,
            "declining": state_declining,
            "method": "result adjusted by opponent tier, match type, recency, and home flag",
            "core_player_continuity": "unavailable",
        },
        "attack_defense": {
            "attack_score": round(attack_score, 1),
            "defense_score": round(defense_score, 1),
            "attack_level": _level(attack_score),
            "defense_level": _level(defense_score),
            "tempo_tendency": "偏保守" if metrics["under_2_5_rate"] >= 0.65 else "偏主动" if metrics["over_2_5_rate"] >= 0.58 else "均衡",
            "goal_for_avg": metrics["goal_for_avg"],
            "goal_against_avg": metrics["goal_against_avg"],
            "clean_sheet_rate": metrics["clean_sheet_rate"],
            "strong_opponent_goal_against_avg": sum(row.goals_against for row in strong_rows) / len(strong_rows) if strong_rows else None,
            "xg": verified_xg or "unavailable",
            "shots": "unavailable",
            "set_piece_strength": "unavailable",
        },
        "tactical_style": {
            "tags": tactical_style_tags,
            "derived_from": ["goal profile", "concession profile", "draw rate", "scoreline distribution"],
        },
        "lineup_players": {
            "lineup_integrity_score": None,
            "attack_integrity_score": None,
            "defense_integrity_score": None,
            "injury_risk_score": None,
            "confirmed_lineup_level": "unavailable",
            "squad_size": verified_squad.get("squad_size") if verified_squad else None,
            "position_counts": verified_squad.get("position_counts") if verified_squad else None,
            "average_caps": verified_squad.get("average_caps") if verified_squad else None,
            "total_caps": verified_squad.get("total_caps") if verified_squad else None,
            "total_goals": verified_squad.get("total_goals") if verified_squad else None,
            "average_height_cm": verified_squad.get("average_height_cm") if verified_squad else None,
            "core_player_dependency": core_player_dependency,
            "top_scorer_status": top_scorer_status,
            "top_scorers_in_squad": verified_squad.get("top_scorers_in_squad") if verified_squad else [],
            "most_capped_players": verified_squad.get("most_capped_players") if verified_squad else [],
            "bench_depth": bench_depth,
            "coach": verified_squad.get("coach") if verified_squad else None,
            "status": "official_squad_available" if verified_squad else "unavailable",
            "note": "Official FIFA squad list is connected; injury, suspension, and starting lineup feeds remain unavailable." if verified_squad else "No verified lineup, injury, suspension, or player availability feed is connected.",
        },
        "environment": {
            "rest_days": rest_days,
            "schedule_fatigue_score": schedule_fatigue_score,
            "environment_adaptation_score": environment_adaptation_score,
            "next_match": schedule_context.get("next_match"),
            "previous_venue": schedule_context.get("previous_venue"),
            "next_venue": schedule_context.get("next_venue"),
            "upcoming_venues": schedule_context.get("upcoming_venues", []),
            "travel_distance_km": travel_distance_km,
            "timezone_shift_hours": timezone_shift_hours,
            "climate_adaptation": climate_adaptation,
            "venue_familiarity": "unavailable",
            "status": "partial" if rest_days is not None or schedule_context.get("upcoming_venues") else "unavailable",
        },
        "data_quality": {
            "score": round(data_quality_score, 1),
            "quality_label": quality_label,
            "source_list": source_list,
            "source_summary": source_summary,
            "rating_source": {
                "source": team_rating.source,
                "effective_date": team_rating.effective_date.isoformat(),
                "elo": team_rating.elo,
                "fifa_rank": team_rating.fifa_rank,
                "fifa_points": team_rating.fifa_points,
            } if team_rating else None,
            "contains_mock": contains_mock,
            "missing_fields": missing_fields,
            "quality_penalties": quality_penalties,
            "updated_at": cutoff.isoformat(),
            "usage_scope": DISPLAY_ONLY_SCOPE,
            "prediction_enabled": False,
            "reproducible": True,
        },
    }
    narrative = {
        "headline": f"长期实力评级：{_strength_grade(long_term_strength_score)}；近期状态：{_level(recent_form_score)}；攻防结构：{_level(attack_score)}/{_level(defense_score)}。",
        "long_term_strength": f"Elo {team_rating.elo:.0f}，近两年正式比赛不败率 {_pct(record['not_loss_rate'])}%，净胜球 {record['goal_difference']}。" if team_rating else "缺少 Elo 评分，长期实力只能弱提示。",
        "recent_form": f"近 5 场 {recent_5_record['wins']}胜{recent_5_record['draws']}平{recent_5_record['losses']}负，场均进球 {recent_goal_for_avg:.2f}，场均失球 {recent_goal_against_avg:.2f}。",
        "attack_defense": f"进攻强度 {_level(attack_score)}，防守稳定性 {_level(defense_score)}，节奏倾向 {modules['attack_defense']['tempo_tendency']}。",
        "tactical_style": f"战术风格标签：{('、'.join(tactical_style_tags) if tactical_style_tags else '样本不足，暂无强标签')}。",
        "lineup_players": "已接入 FIFA 官方 26 人名单、位置深度和国家队经验；伤停、停赛和首发确认仍 unavailable。" if verified_squad else "阵容、伤停、停赛和首发确认数据 unavailable，当前不做主观补全。",
        "environment": f"已接入赛程、场地、旅行距离、时差和历史气候基线；下一场场地 {schedule_context.get('next_match', {}).get('venue') if schedule_context.get('next_match') else 'unavailable'}。实时天气和场地熟悉度仍 unavailable。",
        "data_quality": f"数据可信度 {quality_label}，当前仅用于球队画像展示，不参与预测计算。",
    }
    return {
        "long_term_strength_score": round(long_term_strength_score, 1),
        "recent_form_score": round(recent_form_score, 1),
        "attack_score": round(attack_score, 1),
        "defense_score": round(defense_score, 1),
        "stability_score": round(stability_score, 1),
        "tournament_experience_score": round(tournament_experience_score, 1),
        "lineup_integrity_score": None,
        "injury_risk_score": None,
        "rest_days": rest_days,
        "schedule_fatigue_score": schedule_fatigue_score,
        "environment_adaptation_score": environment_adaptation_score,
        "data_quality_score": round(data_quality_score, 1),
        "tactical_style_tags_json": tactical_style_tags,
        "strong_opponent_performance_json": opponent_performance["strong"],
        "middle_opponent_performance_json": opponent_performance["middle"],
        "weak_opponent_performance_json": opponent_performance["weak"],
        "strengths_json": strengths,
        "weaknesses_json": weaknesses,
        "risk_flags_json": risk_flags,
        "missing_fields_json": missing_fields,
        "source_list_json": source_list,
        "narrative_json": narrative,
        "data_quality_json": modules["data_quality"],
        "profile_modules_json": modules,
        "usage_scope": DISPLAY_ONLY_SCOPE,
        "prediction_enabled": False,
    }


def compute_team_profile(session: Session, team_id: str, as_of_date: datetime) -> TeamProfile:
    team = session.get(Team, team_id)
    if team is None:
        raise LookupError(f"unknown team: {team_id}")
    cutoff = _as_utc(as_of_date)
    rows = list(session.scalars(
        select(TeamProfileMatchHistory)
        .where(TeamProfileMatchHistory.team_id == team_id)
        .where(TeamProfileMatchHistory.match_date <= cutoff.date())
        .order_by(TeamProfileMatchHistory.match_date)
    ))
    competitive = [row for row in rows if not row.is_friendly]
    n = len(competitive)
    tier_stats = tier_statistics(competitive)
    strong_rows = [row for row in competitive if row.opponent_tier in ("elite", "strong")]
    weak_rows = [row for row in competitive if row.opponent_tier == "weak"]
    recent = competitive[-8:]
    world_cup = [row for row in competitive if row.is_world_cup]
    knockout = [row for row in world_cup if row.stage == "knockout"]
    qualifier = [row for row in competitive if row.is_qualifier]
    world_cup_group = [row for row in world_cup if row.stage != "knockout"]
    opening = [row for row in world_cup if row.stage in ("opening", "group")][:max(1, len(world_cup) // 3)]
    goal_for_avg = sum(row.goals_for for row in competitive) / n if n else 0.0
    goal_against_avg = sum(row.goals_against for row in competitive) / n if n else 0.0
    under_25 = rate(competitive, lambda row: row.goals_for + row.goals_against <= 2)
    draw_strong = rate(strong_rows, lambda row: row.result == "draw")
    strong_not_loss = rate(strong_rows, lambda row: row.result != "loss")
    favorite_win = rate(weak_rows, lambda row: row.result == "win")
    team_rating = session.scalar(
        select(TeamRating)
        .where(TeamRating.team_id == team_id)
        .where(TeamRating.effective_date <= cutoff.date())
        .order_by(TeamRating.effective_date.desc(), TeamRating.id.desc())
        .limit(1)
    )
    profile_metrics = {
        "attack_strength_recent": min(1.0, sum(row.goals_for for row in recent) / max(1, len(recent)) / 2.5),
        "defense_strength_recent": max(0.0, 1.0 - sum(row.goals_against for row in recent) / max(1, len(recent)) / 2.5),
        "goal_for_avg": goal_for_avg,
        "goal_against_avg": goal_against_avg,
        "clean_sheet_rate": rate(competitive, lambda row: row.goals_against == 0),
        "failed_to_score_rate": rate(competitive, lambda row: row.goals_for == 0),
        "over_2_5_rate": rate(competitive, lambda row: row.goals_for + row.goals_against >= 3),
        "under_2_5_rate": under_25,
        "both_teams_score_rate": rate(competitive, lambda row: row.goals_for > 0 and row.goals_against > 0),
        "low_score_tendency": under_25,
        "high_score_tendency": rate(competitive, lambda row: row.goals_for + row.goals_against >= 4),
        "draw_rate_overall": rate(competitive, lambda row: row.result == "draw"),
        "draw_rate_vs_elite": tier_stats["elite"]["draw_rate"],
        "draw_rate_vs_strong": tier_stats["strong"]["draw_rate"],
        "draw_rate_as_underdog": draw_strong,
        "draw_resilience_score": min(1.0, draw_strong * 0.65 + strong_not_loss * 0.35),
        "favorite_win_rate": favorite_win,
        "favorite_fail_to_win_rate": 1.0 - favorite_win if weak_rows else 0.0,
        "favorite_overconfidence_risk": (1.0 - favorite_win) * min(1.0, len(weak_rows) / 4) if weak_rows else 0.0,
        "weak_opponent_upset_risk": rate(weak_rows, lambda row: row.result == "loss"),
        "underdog_draw_rate": draw_strong,
        "underdog_win_or_draw_rate": strong_not_loss,
        "upset_potential_score": rate(strong_rows, lambda row: row.result == "win") * 0.6 + strong_not_loss * 0.4,
        "defensive_resilience_score": max(0.0, min(1.0, strong_not_loss * 0.55 + (1.0 - tier_stats["elite"]["goal_against_avg"] / 3.0) * 0.45)),
        "world_cup_experience_score": min(1.0, len(world_cup) / 12),
        "knockout_experience_score": min(1.0, len(knockout) / 5),
        "recent_tournament_consistency": rate(recent, lambda row: row.result != "loss"),
        "pressure_match_score": rate(knockout, lambda row: row.result != "loss"),
        "opening_match_slow_start_score": rate(opening, lambda row: row.result != "win"),
        "group_stage_consistency": rate(world_cup_group, lambda row: row.result != "loss"),
        "third_match_rotation_risk": 0.0,
        "must_win_match_performance": rate(knockout, lambda row: row.result == "win"),
    }
    trait_metrics = {
        **profile_metrics,
        "qualifier_sample_count": len(qualifier),
        "qualifier_win_rate": rate(qualifier, lambda row: row.result == "win"),
        "qualifier_not_loss_rate": rate(qualifier, lambda row: row.result != "loss"),
        "knockout_sample_count": len(knockout),
        "opening_sample_count": len(opening),
        "world_cup_group_sample_count": len(world_cup_group),
        "team_elo": team_rating.elo if team_rating else 1500.0,
    }
    sources = {row.source for row in rows}
    source_summary = _source_summary(sources)
    traits = generate_traits(trait_metrics, n, tier_stats)
    schedule_context = _schedule_context(session, team_id, cutoff)
    statsbomb_xg = _statsbomb_xg_for_team(team)
    fifa_squad = _fifa_squad_for_team(team)
    structured_profile = _build_structured_profile(
        rows=rows,
        competitive=competitive,
        recent=recent,
        world_cup=world_cup,
        knockout=knockout,
        qualifier=qualifier,
        strong_rows=strong_rows,
        weak_rows=weak_rows,
        tier_stats=tier_stats,
        metrics=profile_metrics,
        traits=traits,
        sources=sources,
        source_summary=source_summary,
        team_rating=team_rating,
        schedule_context=schedule_context,
        statsbomb_xg=statsbomb_xg,
        fifa_squad=fifa_squad,
        cutoff=cutoff,
    )
    profile = TeamProfile(
        team_id=team.id, team_code=team.code, profile_version=PROFILE_VERSION,
        profile_as_of=cutoff, data_cutoff=cutoff,
        data_start_year=min((row.match_date.year for row in rows), default=None),
        data_end_year=max((row.match_date.year for row in rows), default=None),
        sample_count=len(rows), world_cup_sample_count=len(world_cup),
        qualifier_sample_count=sum(row.is_qualifier for row in rows), competitive_sample_count=n,
        tier_stats_json=tier_stats, traits_json=traits,
        source_summary_json=source_summary,
        **profile_metrics,
        **structured_profile,
    )
    session.add(profile)
    session.flush()
    return profile


def get_team_profile(session: Session, team_id: str, as_of_date: datetime | None = None) -> TeamProfile | None:
    query = select(TeamProfile).where(TeamProfile.team_id == team_id)
    if as_of_date is not None:
        query = query.where(TeamProfile.profile_as_of <= _as_utc(as_of_date))
    return session.scalar(query.order_by(TeamProfile.profile_as_of.desc(), TeamProfile.id.desc()).limit(1))


def profile_payload(profile: TeamProfile | None) -> dict | None:
    if profile is None:
        return None
    payload = {column.name: getattr(profile, column.name) for column in TeamProfile.__table__.columns if column.name not in {"created_at", "updated_at"}}
    payload["strengths"] = payload.get("strengths_json") or []
    payload["weaknesses"] = payload.get("weaknesses_json") or []
    payload["risk_flags"] = payload.get("risk_flags_json") or []
    payload["missing_fields"] = payload.get("missing_fields_json") or []
    payload["source_list"] = payload.get("source_list_json") or []
    payload["tactical_style_tags"] = payload.get("tactical_style_tags_json") or []
    modules = copy.deepcopy(payload.get("profile_modules_json") or {})
    quality = payload.get("data_quality_json") or {}
    if quality.get("contains_mock"):
        for field in CORE_SCORE_FIELDS:
            payload[field] = None
        payload["traits_json"] = []
        payload["tactical_style_tags"] = []
        payload["strengths"] = []
        payload["weaknesses"] = []
        for key, module in modules.items():
            if key == "data_quality" or not isinstance(module, dict):
                continue
            module["status"] = "mock_data_unavailable"
            module["note"] = "Mock fallback data is not exposed as verified profile scoring."
            for score_key in ("score", "attack_score", "defense_score", "lineup_integrity_score", "injury_risk_score", "rest_days", "schedule_fatigue_score", "environment_adaptation_score"):
                if score_key in module:
                    module[score_key] = None
            if key == "long_term_strength":
                module["grade"] = "unavailable"
            if key == "attack_defense":
                module["attack_level"] = "unavailable"
                module["defense_level"] = "unavailable"
        payload["profile_modules_json"] = modules
    payload["team_profile_narrative"] = payload.get("narrative_json") or {}
    payload["team_profile_data_quality"] = quality
    payload["lineup_integrity_status"] = modules.get("lineup_players", {}).get("status", "unavailable")
    payload["environment_adaptation_status"] = modules.get("environment", {}).get("status", "unavailable")
    return payload


def _source_mode(sources: set[str]) -> str:
    if sources == {"historical_real"}:
        return "historical_real"
    if sources == {"seed_mock_v1"}:
        return "seed_mock_v1"
    return "mixed"


def _source_summary(sources: set[str]) -> dict:
    summary = {"mode": _source_mode(sources), "sources": sorted(sources), "friendly_weight": 0.0}
    if "historical_real" in sources:
        snapshot = load_profile_match_history_snapshot()
        summary.update({
            "provider": snapshot["source"]["provider"],
            "raw_url": snapshot["source"]["raw_url"],
            "raw_sha256": snapshot["source"]["raw_sha256"],
            "date_start": snapshot["coverage"]["date_start"],
            "date_end": snapshot["coverage"]["date_end"],
        })
    return summary


def rebuild_team_profiles(session: Session, as_of_date: datetime | None = None, use_seed: bool = True) -> dict:
    cutoff = _as_utc(as_of_date or datetime.now(timezone.utc))
    if use_seed:
        seed_combined_history(session)
    session.execute(delete(TeamProfile))
    profiles = [compute_team_profile(session, team.id, cutoff) for team in session.scalars(select(Team).order_by(Team.id))]
    data_mode = "historical_real_with_seed_fallback" if use_seed else "existing_history"
    return {"profiles": len(profiles), "profile_version": PROFILE_VERSION, "profile_as_of": cutoff.isoformat(), "data_mode": data_mode}


def explain_team_profile(profile: TeamProfile) -> str:
    if profile.competitive_sample_count < 6:
        return f"仅 {profile.competitive_sample_count} 场正式比赛样本，画像只作弱提示。"
    if profile.narrative_json and profile.narrative_json.get("headline"):
        evidence = (
            f"基于 {profile.competitive_sample_count} 场正式比赛"
            f"（世界杯 {profile.world_cup_sample_count}、预选赛 {profile.qualifier_sample_count}）；"
            f"零封率 {profile.clean_sheet_rate:.0%}，低比分 {profile.under_2_5_rate:.0%}，遇强不败 {profile.underdog_win_or_draw_rate:.0%}。"
        )
        return " ".join([
            evidence,
            profile.narrative_json.get("headline", ""),
            profile.narrative_json.get("long_term_strength", ""),
            profile.narrative_json.get("attack_defense", ""),
            profile.narrative_json.get("data_quality", ""),
        ]).strip()
    traits = "、".join(profile.traits_json) or "未触发强标签"
    samples = f"{profile.competitive_sample_count} 场正式比赛"
    if profile.world_cup_sample_count or profile.qualifier_sample_count:
        samples += f"（世界杯 {profile.world_cup_sample_count}、预选赛 {profile.qualifier_sample_count}）"
    return (
        f"基于 {samples}；画像：{traits}；"
        f"进攻 {profile.goal_for_avg:.2f} 球/场，防守 {profile.goal_against_avg:.2f} 失球/场，"
        f"零封率 {profile.clean_sheet_rate:.0%}；低比分 {profile.under_2_5_rate:.0%}，"
        f"双方进球 {profile.both_teams_score_rate:.0%}；遇强不败 {profile.underdog_win_or_draw_rate:.0%}。"
    )
