from __future__ import annotations

import csv
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.db import session_scope
from app.models import Team, TeamProfile, TeamRating
from app.team_profiles.service import STATSBOMB_XG_SOURCE_TAG, explain_team_profile


ROOT = Path(__file__).resolve().parents[2]
ELO_URL = "https://eloratings.net/World.tsv"
ELO_TEAMS_URL = "https://eloratings.net/en.teams.tsv"
FIFA_RANKING_URL = "https://inside.fifa.com/fifa-world-ranking/men"
REPORT_PATH = ROOT / "artifacts" / "team_profile_validation_report.md"


ALIASES = {
    "Bosnia and Herzegovina": "Bosnia & Herzegovina",
    "Cabo Verde": "Cabo Verde",
    "Cape Verde": "Cabo Verde",
    "Congo DR": "Congo DR",
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


def normalize(value: str) -> str:
    value = ALIASES.get(value, value)
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="ignore")


def load_elo_rankings() -> dict[str, dict]:
    teams = {}
    for row in csv.reader(fetch_text(ELO_TEAMS_URL).splitlines(), delimiter="\t"):
        if len(row) >= 2:
            teams[row[0]] = row[1]

    rankings = {}
    for row in csv.reader(fetch_text(ELO_URL).splitlines(), delimiter="\t"):
        if len(row) < 4:
            continue
        name = teams.get(row[2], row[2])
        canonical = ALIASES.get(name, name)
        rankings[normalize(canonical)] = {
            "rank": int(row[0]),
            "name": canonical,
            "rating": int(row[3]),
        }
    return rankings


def load_fifa_ranking_metadata() -> dict:
    html = fetch_text(FIFA_RANKING_URL)
    official_update = re.search(r"Last official update:</span><span[^>]*>([^<]+)", html)
    if not official_update:
        official_update = re.search(r'"lastUpdateDate":"([^"]+)"', html)
    return {
        "url": FIFA_RANKING_URL,
        "last_official_update": official_update.group(1) if official_update else "unknown",
    }


def load_statsbomb_xg() -> tuple[dict[str, dict], dict]:
    xg_path = ROOT / "data" / "external" / "statsbomb" / "world_cup_xg.json"
    data = json.loads(xg_path.read_text())
    agg = defaultdict(lambda: {"matches": 0, "xg_for": 0.0, "xg_against": 0.0})
    competitions = set()
    seasons = set()
    for match in data.values():
        competitions.add(match.get("competition"))
        seasons.add(str(match.get("season")))
        home = ALIASES.get(match["home_team"], match["home_team"])
        away = ALIASES.get(match["away_team"], match["away_team"])
        agg[normalize(home)]["matches"] += 1
        agg[normalize(home)]["xg_for"] += float(match["home_xg"])
        agg[normalize(home)]["xg_against"] += float(match["away_xg"])
        agg[normalize(away)]["matches"] += 1
        agg[normalize(away)]["xg_for"] += float(match["away_xg"])
        agg[normalize(away)]["xg_against"] += float(match["home_xg"])
    xg = {
        key: {
            "matches": value["matches"],
            "xg_for_avg": value["xg_for"] / value["matches"],
            "xg_against_avg": value["xg_against"] / value["matches"],
        }
        for key, value in agg.items()
        if value["matches"]
    }
    metadata = {
        "match_count": len(data),
        "team_count": len(xg),
        "competitions": sorted(item for item in competitions if item),
        "seasons": sorted(item for item in seasons if item),
        "source_file": str(xg_path.relative_to(ROOT)),
    }
    return xg, metadata


def latest_team_rating(session, team_id: str) -> float | None:
    row = session.scalar(
        select(TeamRating)
        .where(TeamRating.team_id == team_id)
        .order_by(TeamRating.effective_date.desc(), TeamRating.id.desc())
        .limit(1)
    )
    return row.elo if row else None


def validate_profile(team: Team, profile: TeamProfile, team_elo: float | None, external_elo: dict | None, xg: dict | None) -> list[str]:
    issues = []
    traits = set(profile.traits_json or [])
    external_rating = external_elo["rating"] if external_elo else None

    if "进攻火力顶级" in traits:
        if (team_elo or 0) < 1800 and (external_rating or 0) < 1800:
            issues.append("顶级进攻标签与本地/Elo强度评级均不匹配，可能是弱对手样本抬高进球均值")
        if xg and xg["matches"] >= 3 and xg["xg_for_avg"] < 1.1 and profile.goal_for_avg < 1.8:
            issues.append("顶级进攻标签与StatsBomb世界杯xG不匹配")

    if "进攻火力强" in traits and xg and xg["matches"] >= 3 and xg["xg_for_avg"] < 1.0 and profile.goal_for_avg < 1.7:
        issues.append("强进攻标签与StatsBomb世界杯xG偏弱冲突")

    if {"防线稳固", "零封能力强"} & traits and xg and xg["matches"] >= 3 and xg["xg_against_avg"] > 1.6 and profile.goal_against_avg > 1.0:
        issues.append("防守标签与StatsBomb世界杯xGA偏高冲突")

    if "大赛经验丰富" in traits and profile.world_cup_sample_count < 8:
        issues.append("大赛经验丰富标签样本不足")

    if "淘汰赛履历强" in traits and profile.knockout_experience_score < 0.4:
        issues.append("淘汰赛履历强标签与淘汰赛样本分不匹配")

    if "遇强韧性高" in traits and profile.underdog_win_or_draw_rate < 0.65:
        issues.append("遇强韧性高标签与遇强不败率不匹配")

    return issues


def main() -> None:
    elo_rankings = load_elo_rankings()
    fifa_meta = load_fifa_ranking_metadata()
    statsbomb_xg, statsbomb_meta = load_statsbomb_xg()

    rows = []
    issues = []
    with session_scope() as session:
        profiles = list(session.scalars(select(TeamProfile).order_by(TeamProfile.team_code)))
        for profile in profiles:
            team = session.get(Team, profile.team_id)
            key = normalize(team.name)
            external_elo = elo_rankings.get(key)
            xg = statsbomb_xg.get(key)
            attack_defense = (profile.profile_modules_json or {}).get("attack_defense", {})
            profile_xg = attack_defense.get("xg")
            profile_has_xg = isinstance(profile_xg, dict)
            source_list = profile.source_list_json or []
            missing_fields = profile.missing_fields_json or []
            team_elo = latest_team_rating(session, team.id)
            profile_issues = validate_profile(team, profile, team_elo, external_elo, xg)
            if xg and not profile_has_xg:
                profile_issues.append("StatsBomb xG source matches team but profile attack_defense.xg is unavailable")
            if not xg and profile_has_xg:
                profile_issues.append("Profile exposes StatsBomb xG for a team not covered by the local source")
            if profile_has_xg and STATSBOMB_XG_SOURCE_TAG not in source_list:
                profile_issues.append("StatsBomb xG is exposed but source_list is missing the xG source tag")
            if profile_has_xg and "xg" in missing_fields:
                profile_issues.append("StatsBomb xG is exposed but xg remains in missing_fields")
            if not profile_has_xg and "xg" not in missing_fields:
                profile_issues.append("StatsBomb xG is unavailable but xg is absent from missing_fields")
            if profile_issues:
                issues.append((team.code, team.name, profile_issues, profile.traits_json))
            rows.append({
                "code": team.code,
                "name": team.name,
                "traits": profile.traits_json or [],
                "source_list": source_list,
                "data_quality_score": profile.data_quality_score,
                "fifa_rank": (profile.profile_modules_json or {}).get("long_term_strength", {}).get("fifa_rank"),
                "travel_distance_km": (profile.profile_modules_json or {}).get("environment", {}).get("travel_distance_km"),
                "timezone_shift_hours": (profile.profile_modules_json or {}).get("environment", {}).get("timezone_shift_hours"),
                "environment_adaptation_score": profile.environment_adaptation_score,
                "climate_adaptation": (profile.profile_modules_json or {}).get("environment", {}).get("climate_adaptation"),
                "squad_size": (profile.profile_modules_json or {}).get("lineup_players", {}).get("squad_size"),
                "team_elo": team_elo,
                "world_elo_rank": external_elo["rank"] if external_elo else None,
                "world_elo_rating": external_elo["rating"] if external_elo else None,
                "statsbomb_matches": xg["matches"] if xg else 0,
                "statsbomb_xg_for": xg["xg_for_avg"] if xg else None,
                "statsbomb_xg_against": xg["xg_against_avg"] if xg else None,
                "profile_statsbomb_xg": profile_xg,
                "explanation": explain_team_profile(profile),
            })

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    avg_traits = sum(len(row["traits"]) for row in rows) / len(rows)
    covered_by_elo = sum(1 for row in rows if row["world_elo_rank"] is not None)
    covered_by_xg = sum(1 for row in rows if row["statsbomb_matches"])
    covered_by_profile_xg = sum(1 for row in rows if isinstance(row["profile_statsbomb_xg"], dict))
    covered_by_fifa = sum(1 for row in rows if row["fifa_rank"] is not None)
    covered_by_travel = sum(1 for row in rows if row["travel_distance_km"] is not None)
    covered_by_timezone = sum(1 for row in rows if row["timezone_shift_hours"] is not None)
    covered_by_environment = sum(1 for row in rows if row["environment_adaptation_score"] is not None)
    covered_by_climate = sum(1 for row in rows if isinstance(row["climate_adaptation"], dict))
    covered_by_squad = sum(1 for row in rows if row["squad_size"] == 26)
    traceable_sources = sum(1 for row in rows if row["source_list"])
    untraceable = [row for row in rows if not row["source_list"]]
    for row in untraceable:
        issues.append((row["code"], row["name"], ["source_list_json is empty"], row["traits"]))
    if statsbomb_meta["competitions"] != ["World Cup"]:
        issues.append(("GLOBAL", "StatsBomb xG", [f"Unexpected competitions: {statsbomb_meta['competitions']}"], []))
    if statsbomb_meta["seasons"] != ["2018", "2022"]:
        issues.append(("GLOBAL", "StatsBomb xG", [f"Unexpected seasons: {statsbomb_meta['seasons']}"], []))
    lines = [
        "# Team Profile Trait Validation",
        "",
        "## Sources",
        f"- Local profile database: {len(rows)} teams, generated from the current workspace database.",
        f"- World Football Elo Ratings: `{ELO_URL}`, matched {covered_by_elo}/{len(rows)} teams.",
        f"- FIFA official ranking page: `{FIFA_RANKING_URL}`, last official update `{fifa_meta['last_official_update']}`, profile rank coverage {covered_by_fifa}/{len(rows)} teams.",
        f"- StatsBomb World Cup xG file: `{statsbomb_meta['source_file']}`, {statsbomb_meta['match_count']} matches, seasons {', '.join(statsbomb_meta['seasons'])}, competitions {', '.join(statsbomb_meta['competitions'])}, source matched {covered_by_xg}/{len(rows)} teams, profile exposes xG for {covered_by_profile_xg}/{len(rows)} teams.",
        f"- 2026 venue registry: `data/seed/world-cup-2026-venues.json`, travel distance {covered_by_travel}/{len(rows)}, timezone shift {covered_by_timezone}/{len(rows)}, environment score {covered_by_environment}/{len(rows)} teams.",
        f"- Open-Meteo historical climate baseline: `data/seed/world-cup-2026-venue-climate.json`, climate baseline {covered_by_climate}/{len(rows)} teams.",
        f"- FIFA official squad list: `data/seed/world-cup-2026-squads.json`, squad coverage {covered_by_squad}/{len(rows)} teams.",
        f"- Traceable profile source_list coverage: {traceable_sources}/{len(rows)} teams.",
        "",
        "## Summary",
        f"- Average trait count: {avg_traits:.2f}",
        f"- Data quality score range: {min(row['data_quality_score'] for row in rows):.1f} - {max(row['data_quality_score'] for row in rows):.1f}",
        f"- Teams with validation issues: {len(issues)}",
        "",
        "## Issues",
    ]
    if issues:
        for code, name, profile_issues, traits in issues:
            lines.append(f"- `{code}` {name}: {'; '.join(profile_issues)}. Traits: {', '.join(traits)}")
    else:
        lines.append("- No validation issues detected by the current checks.")

    lines.extend(["", "## Sample Rows", ""])
    for row in sorted(rows, key=lambda item: item["code"]):
        lines.append(
            f"- `{row['code']}` {row['name']}: Elo={row['team_elo']:.0f} / "
            f"WorldEloRank={row['world_elo_rank'] or 'NA'}, traits={', '.join(row['traits'])}"
        )

    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print({
        "teams": len(rows),
        "avg_traits": round(avg_traits, 2),
        "elo_matches": covered_by_elo,
        "fifa_rank_matches": covered_by_fifa,
        "statsbomb_xg_matches": covered_by_xg,
        "profile_statsbomb_xg_matches": covered_by_profile_xg,
        "travel_distance_matches": covered_by_travel,
        "timezone_shift_matches": covered_by_timezone,
        "environment_score_matches": covered_by_environment,
        "climate_baseline_matches": covered_by_climate,
        "squad_matches": covered_by_squad,
        "issues": len(issues),
        "report": str(REPORT_PATH),
    })


if __name__ == "__main__":
    main()
