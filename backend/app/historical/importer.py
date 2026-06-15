"""Import international_results.csv into the HistoricalMatch table.

Reads the martj42/international_results CSV and shootouts.csv,
maps country names to FIFA 3-letter team codes, and stores
enriched match records for the profile engine.

Supports both WC teams (in the teams table) and non-WC national teams
(stored in the historical_teams table).
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.models import HistoricalMatch, HistoricalTeam, Team, TeamAlias

logger = logging.getLogger(__name__)

PROVIDER = "martj42"
EXTERNAL_DIR = PROJECT_ROOT / "data" / "external"
IMPORT_SINCE = "2018-01-01"

# ── competition classification ──────────────────────────────────────────

_WORLD_CUP_KEYWORDS = ("FIFA World Cup",)
_QUALIFIER_KEYWORDS = ("FIFA World Cup qualification",)
_CONTINENTAL_KEYWORDS = (
    "UEFA Euro",
    "Copa América",
    "African Cup of Nations",
    "CONCACAF Gold Cup",
    "AFC Asian Cup",
    "Oceania Nations Cup",
)
_CONTINENTAL_QUALIFIER_KEYWORDS = (
    "UEFA Euro qualification",
    "Copa América qualification",
    "African Cup of Nations qualification",
    "CONCACAF Gold Cup qualification",
    "AFC Asian Cup qualification",
    "Oceania Nations Cup qualification",
)

_IMPORTANCE_MAP = {
    "friendly": 1.0,
    "qualifier": 1.5,
    "continental_qualifier": 1.5,
    "continental": 2.0,
    "world_cup": 3.0,
    "other": 1.0,
}


def classify_tournament(tournament: str) -> tuple[str, float]:
    """Return (competition_type, match_importance) from tournament name."""
    for kw in _WORLD_CUP_KEYWORDS:
        if kw in tournament and "qualification" not in tournament:
            return "world_cup", 3.0
    for kw in _QUALIFIER_KEYWORDS:
        if kw in tournament:
            return "qualifier", 1.5
    for kw in _CONTINENTAL_QUALIFIER_KEYWORDS:
        if kw in tournament:
            return "continental_qualifier", 1.5
    for kw in _CONTINENTAL_KEYWORDS:
        if kw in tournament and "qualification" not in tournament:
            return "continental", 2.0
    if tournament == "Friendly":
        return "friendly", 1.0
    return "other", 1.0


# ── team name mapping ───────────────────────────────────────────────────

# FIFA member national teams with their 3-letter codes.
# This covers ALL FIFA members that appear in the CSV, not just the 48 WC teams.
_FIFA_MEMBER_CODES: dict[str, str] = {
    # South America
    "Argentina": "ARG",
    "Brazil": "BRA",
    "Chile": "CHI",
    "Colombia": "COL",
    "Ecuador": "ECU",
    "Paraguay": "PAR",
    "Peru": "PER",
    "Uruguay": "URU",
    "Venezuela": "VEN",
    "Bolivia": "BOL",
    # Europe
    "England": "ENG",
    "France": "FRA",
    "Germany": "GER",
    "Italy": "ITA",
    "Spain": "ESP",
    "Portugal": "POR",
    "Netherlands": "NED",
    "Belgium": "BEL",
    "Croatia": "CRO",
    "Serbia": "SRB",
    "Switzerland": "SUI",
    "Austria": "AUT",
    "Poland": "POL",
    "Ukraine": "UKR",
    "Sweden": "SWE",
    "Denmark": "DEN",
    "Norway": "NOR",
    "Finland": "FIN",
    "Iceland": "ISL",
    "Greece": "GRE",
    "Romania": "ROU",
    "Hungary": "HUN",
    "Bulgaria": "BUL",
    "Slovakia": "SVK",
    "Slovenia": "SVN",
    "Israel": "ISR",
    "Albania": "ALB",
    "Georgia": "GEO",
    "Armenia": "ARM",
    "Azerbaijan": "AZE",
    "Belarus": "BLR",
    "Luxembourg": "LUX",
    "Montenegro": "MNE",
    "Moldova": "MDA",
    "Kosovo": "KVX",
    "Cyprus": "CYP",
    "Malta": "MLT",
    "Estonia": "EST",
    "Latvia": "LVA",
    "Lithuania": "LTU",
    "Faroe Islands": "FRO",
    "Andorra": "AND",
    "San Marino": "SMR",
    "Gibraltar": "GIB",
    "Liechtenstein": "LIE",
    "Wales": "WAL",
    "Scotland": "SCO",
    "Northern Ireland": "NIR",
    "Republic of Ireland": "IRL",
    "Czech Republic": "CZE",
    "Russia": "RUS",
    "Bosnia and Herzegovina": "BIH",
    "North Macedonia": "MKD",
    "Türkiye": "TUR",
    "Turkey": "TUR",
    # Africa
    "Egypt": "EGY",
    "Algeria": "ALG",
    "Tunisia": "TUN",
    "Morocco": "MAR",
    "Libya": "LBY",
    "Nigeria": "NGA",
    "Ghana": "GHA",
    "Senegal": "SEN",
    "Mali": "MLI",
    "Burkina Faso": "BFA",
    "Niger": "NIG",
    "Togo": "TOG",
    "Benin": "BEN",
    "Cameroon": "CMR",
    "Gabon": "GAB",
    "Congo": "COG",
    "Congo Republic": "COG",
    "DR Congo": "COD",
    "Congo DR": "COD",
    "Equatorial Guinea": "EQG",
    "Guinea": "GUI",
    "Guinea-Bissau": "GNB",
    "Cape Verde": "CPV",
    "Liberia": "LBR",
    "Sierra Leone": "SLE",
    "Gambia": "GAM",
    "Mauritania": "MTN",
    "São Tomé and Príncipe": "STP",
    "Sao Tome and Principe": "STP",
    "Comoros": "COM",
    "Chad": "CHA",
    "Rwanda": "RWA",
    "Burundi": "BDI",
    "Malawi": "MWI",
    "Botswana": "BOT",
    "Lesotho": "LES",
    "Mozambique": "MOZ",
    "Angola": "ANG",
    "Namibia": "NAM",
    "Madagascar": "MAD",
    "Mauritius": "MRI",
    "Seychelles": "SEY",
    "Tanzania": "TAN",
    "Uganda": "UGA",
    "Kenya": "KEN",
    "Ethiopia": "ETH",
    "Somalia": "SOM",
    "Djibouti": "DJI",
    "Eritrea": "ERI",
    "South Sudan": "SSD",
    "Sudan": "SUD",
    "Central African Republic": "CTA",
    "Zambia": "ZAM",
    "Zimbabwe": "ZIM",
    "Ivory Coast": "CIV",
    # Asia
    "Japan": "JPN",
    "South Korea": "KOR",
    "Iran": "IRN",
    "Saudi Arabia": "KSA",
    "Iraq": "IRQ",
    "United Arab Emirates": "UAE",
    "Qatar": "QAT",
    "Kuwait": "KUW",
    "Bahrain": "BHR",
    "Oman": "OMA",
    "Jordan": "JOR",
    "Lebanon": "LBN",
    "Yemen": "YEM",
    "Syria": "SYR",
    "Palestine": "PLE",
    "China PR": "CHN",
    "Uzbekistan": "UZB",
    "Kazakhstan": "KAZ",
    "Tajikistan": "TJK",
    "Turkmenistan": "TKM",
    "Kyrgyz Republic": "KGZ",
    "Kyrgyzstan": "KGZ",
    "Afghanistan": "AFG",
    "Pakistan": "PAK",
    "India": "IND",
    "Bangladesh": "BAN",
    "Nepal": "NEP",
    "Sri Lanka": "SRI",
    "Maldives": "MDV",
    "Myanmar": "MYA",
    "Thailand": "THA",
    "Malaysia": "MAS",
    "Singapore": "SIN",
    "Indonesia": "IDN",
    "Philippines": "PHI",
    "Vietnam": "VNM",
    "Vietnam Republic": "VNM",
    "Laos": "LAO",
    "Cambodia": "CAM",
    "Chinese Taipei": "TPE",
    "North Korea": "PRK",
    "Mongolia": "MNG",
    "Brunei": "BRU",
    "Timor-Leste": "TLS",
    # North & Central America
    "United States": "USA",
    "Mexico": "MEX",
    "Canada": "CAN",
    "Costa Rica": "CRC",
    "Panama": "PAN",
    "Honduras": "HON",
    "El Salvador": "SLV",
    "Jamaica": "JAM",
    "Cuba": "CUB",
    "Dominican Republic": "DOM",
    "Trinidad and Tobago": "TRI",
    "Haiti": "HAI",
    "Suriname": "SUR",
    "Guyana": "GUY",
    "Belize": "BLZ",
    "Nicaragua": "NCA",
    "Guatemala": "GUA",
    "Saint Kitts and Nevis": "SKN",
    "Saint Vincent and the Grenadines": "VIN",
    "Saint Lucia": "LCA",
    "Grenada": "GRN",
    "Dominica": "DMA",
    "Barbados": "BRB",
    "Antigua and Barbuda": "ATG",
    "Bahamas": "BAH",
    "Bermuda": "BER",
    "Cayman Islands": "CAY",
    "British Virgin Islands": "VGB",
    "Puerto Rico": "PUR",
    "Martinique": "MTQ",
    "Guadeloupe": "GLP",
    "French Guiana": "GUF",
    "Aruba": "ABW",
    "Curaçao": "CUW",
    "Curacao": "CUW",
    # Oceania
    "Australia": "AUS",
    "New Zealand": "NZL",
    "Solomon Islands": "SOL",
    "New Caledonia": "NCL",
    "Fiji": "FIJ",
    "Papua New Guinea": "PNG",
    "Vanuatu": "VAN",
    "Tahiti": "TAH",
    "Samoa": "SAM",
    "Cook Islands": "COK",
    "Tonga": "TGA",
    "American Samoa": "ASA",
    "Guam": "GUM",
    "Northern Mariana Islands": "NMI",
}

# Non-FIFA representative teams (not full FIFA members but recognized teams)
_NON_FIFA_REPRESENTATIVES: set[str] = {
    "Catalonia",
    "Basque Country",
    "Galicia",
    "Andalusia",
    "Catalonia XI",
    "Basque Country XI",
    "Zanzibar",
    "Greenland",
    "Gibraltar United",
    "Kárpátalja",
    "Occitania",
    "Padania",
    "Sealand",
    "Sápmi",
    "Somaliland",
    "Western Sahara",
    "Ynys Môn",
    "Two Sicilies",
    "Tamil Eelam",
    "Chagos Islands",
    "Aldabra Islands",
    "Barawa",
    "Darfur",
    "Ellan Vannin",
    "Kernow",
    "Matabeleland",
    "Provence",
    "Raetia",
    "Székely Land",
    "United Koreans in Japan",
}

# Regional / sub-national teams
_REGIONAL_TEAMS: set[str] = {
    "Yorkshire",
    "Isle of Man",
    "Isle of Wight",
    "Jersey",
    "Guernsey",
    "Shetland Islands",
    "Orkney Islands",
    "Western Isles",
    "Gothia Cup",
    "Middlesex",
    "Kent",
    "Essex",
    "Surrey",
    "Lancashire",
    "Staffordshire",
    "Norfolk",
    "Cornwall",
    "Devon",
    "Somerset",
    "Dorset",
    "Wiltshire",
    "Gloucestershire",
    "Oxfordshire",
    "Buckinghamshire",
    "Hertfordshire",
    "Cambridgeshire",
    "Lincolnshire",
    "Nottinghamshire",
    "Derbyshire",
    "Cheshire",
    "Cumbria",
    "Northumberland",
    "Durham",
    "Warwickshire",
    "Worcestershire",
    "Herefordshire",
    "Shropshire",
    "Leicestershire",
    "Rutland",
    "Northamptonshire",
    "Bedfordshire",
    "Berkshire",
    "Hampshire",
    "Sussex",
    "East Sussex",
    "West Sussex",
    "Suffolk",
    "North Yorkshire",
    "South Yorkshire",
    "West Yorkshire",
    "East Riding of Yorkshire",
    "Isle of Anglesey",
    "Gwynedd",
    "Conwy",
    "Denbighshire",
    "Flintshire",
    "Wrexham",
    "Powys",
    "Ceredigion",
    "Pembrokeshire",
    "Carmarthenshire",
    "Swansea",
    "Neath Port Talbot",
    "Bridgend",
    "Vale of Glamorgan",
    "Rhondda Cynon Taf",
    "Merthyr Tydfil",
    "Caerphilly",
    "Blaenau Gwent",
    "Torfaen",
    "Monmouthshire",
    "Newport",
    "Cardiff",
    "Clackmannanshire",
    "Stirling",
    "Falkirk",
    "West Lothian",
    "Edinburgh",
    "Midlothian",
    "East Lothian",
    "Scottish Borders",
    "Dumfries and Galloway",
    "South Ayrshire",
    "East Ayrshire",
    "North Ayrshire",
    "Inverclyde",
    "Renfrewshire",
    "East Renfrewshire",
    "Glasgow",
    "North Lanarkshire",
    "South Lanarkshire",
    "Fife",
    "Dundee",
    "Angus",
    "Aberdeenshire",
    "Aberdeen",
    "Moray",
    "Highland",
    "Na h-Eileanan Siar",
    "Orkney",
    "Shetland",
    "Antrim",
    "Armagh",
    "Down",
    "Fermanagh",
    "Londonderry",
    "Tyrone",
    "Derry City",
}

# Historical renamed countries - maps former name to current name
# This is populated from former_names.csv at import time
_FORMER_NAMES: dict[str, str] = {}


def _load_former_names() -> dict[str, str]:
    """Load former_names.csv into a mapping from former_name -> current_name."""
    mapping: dict[str, str] = {}
    csv_path = EXTERNAL_DIR / "former_names.csv"
    if not csv_path.exists():
        return mapping
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # CSV has columns: current, former, start_date, end_date
            former = row.get("former", row.get("former_name", "")).strip()
            current = row.get("current", row.get("current_name", "")).strip()
            if former and current:
                mapping[former] = current
    return mapping


def _classify_team(name: str) -> str:
    """Classify a team name into a category."""
    if name in _NON_FIFA_REPRESENTATIVES:
        return "non_fifa_representative"
    if name in _REGIONAL_TEAMS:
        return "regional"
    if name in _FIFA_MEMBER_CODES:
        return "fifa_member"
    # Check if it's a former name of a known country
    if name in _FORMER_NAMES:
        return "historical_renamed"
    return "unknown"


def _make_historical_team_id(code: str) -> str:
    """Generate a HistoricalTeam id from a code."""
    return f"ht_{code}"


def _build_name_to_source_map(session: Session) -> dict[str, tuple[str, str]]:
    """Build a comprehensive mapping from country name -> (team_id, source).

    source is either "world_cup" (for teams in the WC teams table)
    or "historical" (for teams in the historical_teams table or FIFA members
    that aren't in the WC teams table).

    Returns:
        dict mapping name -> (team_id, source)
    """
    mapping: dict[str, tuple[str, str]] = {}

    # 1. From teams table (WC teams)
    for team in session.scalars(select(Team)):
        mapping[team.name] = (team.id, "world_cup")
        mapping[team.short_name] = (team.id, "world_cup")
        mapping[team.code] = (team.id, "world_cup")

    # 2. From TeamAlias table
    for alias_row in session.scalars(select(TeamAlias)):
        if alias_row.alias not in mapping:
            mapping[alias_row.alias] = (alias_row.team_id, "world_cup")

    # 3. From existing HistoricalTeam records
    for ht in session.scalars(select(HistoricalTeam)):
        mapping[ht.name] = (ht.id, "historical")
        for alias in (ht.aliases or []):
            if alias not in mapping:
                mapping[alias] = (ht.id, "historical")

    # 4. From FIFA member codes (for teams not in WC teams table)
    # First, collect all WC team IDs for quick lookup
    wc_team_ids = {team.id for team in session.scalars(select(Team))}

    for name, code in _FIFA_MEMBER_CODES.items():
        if name not in mapping:
            # If the FIFA code matches a WC team, use the WC team ID directly
            if code in wc_team_ids:
                mapping[name] = (code, "world_cup")
            else:
                ht_id = _make_historical_team_id(code)
                mapping[name] = (ht_id, "historical")

    # 5. From former names - map to current country's code
    for former, current in _FORMER_NAMES.items():
        if former not in mapping:
            # Find the current country's code
            if current in _FIFA_MEMBER_CODES:
                code = _FIFA_MEMBER_CODES[current]
                if code in wc_team_ids:
                    mapping[former] = (code, "world_cup")
                else:
                    ht_id = _make_historical_team_id(code)
                    mapping[former] = (ht_id, "historical")
            elif current in mapping:
                # Current name maps to something already known
                mapping[former] = mapping[current]

    return mapping


def _build_name_to_code_map(session: Session) -> dict[str, str]:
    """Build a mapping from country name -> team id (3-letter code).

    Uses Team.name, Team.short_name, Team.code, TeamAlias entries,
    and a hardcoded FIFA_MEMBER_CODES fallback.

    This function returns only the team_id for backward compatibility
    with existing callers (e.g., tests). For the full (team_id, source)
    mapping, use _build_name_to_source_map.
    """
    source_map = _build_name_to_source_map(session)
    return {name: team_id for name, (team_id, _source) in source_map.items()}


def _ensure_historical_teams(
    session: Session,
    name_map: dict[str, tuple[str, str]],
    all_team_names: set[str],
) -> int:
    """Create HistoricalTeam records for any team that doesn't have one yet.

    Only creates records for teams that are mapped to "historical" source
    and don't already exist in the historical_teams table.

    Returns:
        Number of new HistoricalTeam records created.
    """
    # Get existing historical team IDs
    existing_ht_ids = set(
        session.scalars(select(HistoricalTeam.id))
    )

    # Also get existing WC team IDs to set current_team_id
    wc_team_ids = set(session.scalars(select(Team.id)))

    created = 0
    seen_ids: set[str] = set()

    for name in all_team_names:
        if name not in name_map:
            continue

        team_id, source = name_map[name]
        if source != "historical":
            continue
        if team_id in existing_ht_ids or team_id in seen_ids:
            continue

        # Determine category
        category = _classify_team(name)

        # Determine current_team_id (link to WC team if applicable)
        current_team_id = None
        former_name_of = None

        # Check if this is a former name of a current country
        if name in _FORMER_NAMES:
            current_name = _FORMER_NAMES[name]
            if current_name in _FIFA_MEMBER_CODES:
                code = _FIFA_MEMBER_CODES[current_name]
                if code in wc_team_ids:
                    current_team_id = code
                former_name_of = _make_historical_team_id(code)
        elif name in _FIFA_MEMBER_CODES:
            code = _FIFA_MEMBER_CODES[name]
            if code in wc_team_ids:
                current_team_id = code

        # Determine provider_team_id
        if name in _FIFA_MEMBER_CODES:
            provider_team_id = _FIFA_MEMBER_CODES[name]
        else:
            provider_team_id = name

        ht = HistoricalTeam(
            id=team_id,
            name=name,
            provider=PROVIDER,
            provider_team_id=provider_team_id,
            team_category=category,
            current_team_id=current_team_id,
            former_name_of=former_name_of,
            aliases=[],
            is_active=True,
        )
        session.add(ht)
        seen_ids.add(team_id)
        created += 1

    if created:
        session.flush()

    return created


# ── import statistics ───────────────────────────────────────────────────

@dataclass
class ImportStats:
    total_csv_rows: int = 0
    filtered_by_date: int = 0
    filtered_future: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    unmapped_teams: int = 0
    penalty_matches: int = 0
    errors: int = 0
    unmapped_names: set[str] = field(default_factory=set)
    historical_teams_created: int = 0


# ── main import function ────────────────────────────────────────────────

def import_historical_matches(session: Session, since: str = IMPORT_SINCE) -> ImportStats:
    """Import international_results.csv into HistoricalMatch.

    Args:
        session: DB session.
        since: ISO date string; only import matches on or after this date.

    Returns:
        ImportStats with counts.
    """
    stats = ImportStats()

    # Load former names mapping
    global _FORMER_NAMES
    _FORMER_NAMES = _load_former_names()

    # First pass: scan CSV to find all unique team names
    csv_path = EXTERNAL_DIR / "international_results.csv"
    if not csv_path.exists():
        logger.error("CSV not found: %s", csv_path)
        stats.errors += 1
        return stats

    since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)

    # Scan for all team names in the date range
    all_team_names: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row["date"].strip()
            try:
                match_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if match_date < since_dt or match_date > now:
                continue
            home_raw = row.get("home_team", "").strip()
            away_raw = row.get("away_team", "").strip()
            if home_raw:
                all_team_names.add(home_raw)
            if away_raw:
                all_team_names.add(away_raw)

    # Build comprehensive name map
    name_map = _build_name_to_source_map(session)

    # Create HistoricalTeam records for any newly discovered teams
    stats.historical_teams_created = _ensure_historical_teams(session, name_map, all_team_names)

    # Rebuild name map after creating historical teams
    name_map = _build_name_to_source_map(session)

    # Load shootout data for penalty info
    shootouts = _load_shootouts()

    # Collect existing source_match_ids for idempotency
    existing_ids = set(
        session.scalars(
            select(HistoricalMatch.source_match_id).where(
                HistoricalMatch.provider == PROVIDER
            )
        )
    )

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats.total_csv_rows += 1
            try:
                date_str = row["date"].strip()
                match_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

                # Filter by date range
                if match_date < since_dt:
                    stats.filtered_by_date += 1
                    continue

                # Filter future matches
                if match_date > now:
                    stats.filtered_future += 1
                    continue

                home_raw = row["home_team"].strip()
                away_raw = row["away_team"].strip()
                home_score_str = row["home_score"].strip()
                away_score_str = row["away_score"].strip()
                # Skip rows with missing scores (NA values)
                if not home_score_str or not away_score_str or home_score_str == "NA" or away_score_str == "NA":
                    stats.filtered_by_date += 1  # count as skipped
                    continue
                home_score = int(home_score_str)
                away_score = int(away_score_str)
                tournament = row["tournament"].strip()
                city = row.get("city", "").strip() or None
                country = row.get("country", "").strip() or None
                neutral = row.get("neutral", "").strip().upper() == "TRUE"

                source_match_id = f"{date_str}_{home_raw}_{away_raw}"

                # Idempotency check
                if source_match_id in existing_ids:
                    stats.skipped_existing += 1
                    continue

                # Team mapping - now returns (team_id, source) or None
                home_mapped = name_map.get(home_raw)
                away_mapped = name_map.get(away_raw)

                home_team_id = home_mapped[0] if home_mapped else None
                home_team_source = home_mapped[1] if home_mapped else "unknown"
                away_team_id = away_mapped[0] if away_mapped else None
                away_team_source = away_mapped[1] if away_mapped else "unknown"

                # is_unmapped is True only when BOTH teams are truly unknown
                is_unmapped = (home_team_id is None) and (away_team_id is None)
                if home_team_id is None or away_team_id is None:
                    stats.unmapped_teams += 1
                    if home_team_id is None:
                        stats.unmapped_names.add(home_raw)
                    if away_team_id is None:
                        stats.unmapped_names.add(away_raw)

                # Competition classification
                competition_type, match_importance = classify_tournament(tournament)

                # Penalty shootout data
                shootout_key = (date_str, home_raw, away_raw)
                shootout_info = shootouts.get(shootout_key)
                went_to_penalties = shootout_info is not None
                penalty_winner = None
                went_to_extra_time = False
                home_score_90min = None
                away_score_90min = None

                if went_to_penalties:
                    stats.penalty_matches += 1
                    penalty_winner = shootout_info["winner"]
                    went_to_extra_time = True

                # Determine score_scope:
                # - "full_90min": we're confident the score represents the 90-minute result
                # - "after_extra_time_or_unknown": the score includes extra time (or we can't tell)
                # - "unknown_score_scope": for future data sources where we can't determine the scope
                # For this CSV: matches in shootouts.csv => penalties => extra time => after_extra_time_or_unknown
                # All other matches => full_90min (we assume no extra time for regular matches)
                score_scope = "after_extra_time_or_unknown" if went_to_penalties else "full_90min"

                match = HistoricalMatch(
                    source_match_id=source_match_id,
                    provider=PROVIDER,
                    kickoff=match_date,
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    home_team_source=home_team_source,
                    away_team_source=away_team_source,
                    home_team_raw=home_raw,
                    away_team_raw=away_raw,
                    home_score=home_score,
                    away_score=away_score,
                    home_score_90min=home_score_90min,
                    away_score_90min=away_score_90min,
                    neutral_venue=neutral,
                    competition=tournament,
                    competition_type=competition_type,
                    match_importance=match_importance,
                    went_to_extra_time=went_to_extra_time,
                    went_to_penalties=went_to_penalties,
                    penalty_winner=penalty_winner,
                    city=city,
                    country=country,
                    is_unmapped=is_unmapped,
                    time_precision="date_only",
                    available_at=match_date + timedelta(days=1),
                    score_scope=score_scope,
                    raw_payload={
                        "date": date_str,
                        "home_team": home_raw,
                        "away_team": away_raw,
                        "home_score": home_score,
                        "away_score": away_score,
                        "tournament": tournament,
                        "city": city,
                        "country": country,
                        "neutral": neutral,
                    },
                )
                session.add(match)
                existing_ids.add(source_match_id)
                stats.inserted += 1

            except Exception as e:
                logger.warning("Failed to import row %d: %s", stats.total_csv_rows, e)
                stats.errors += 1

    session.flush()

    if stats.unmapped_names:
        logger.info(
            "Unmapped team names (%d): %s",
            len(stats.unmapped_names),
            ", ".join(sorted(stats.unmapped_names)[:30]),
        )

    logger.info(
        "Import complete: %d inserted, %d skipped, %d unmapped, %d penalties, %d errors, %d historical teams created",
        stats.inserted, stats.skipped_existing, stats.unmapped_teams,
        stats.penalty_matches, stats.errors, stats.historical_teams_created,
    )
    return stats


def _load_shootouts() -> dict[tuple[str, str, str], dict]:
    """Load shootouts.csv into a lookup dict keyed by (date, home, away)."""
    result: dict[tuple[str, str, str], dict] = {}
    csv_path = EXTERNAL_DIR / "shootouts.csv"
    if not csv_path.exists():
        logger.warning("shootouts.csv not found: %s", csv_path)
        return result

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["date"].strip(), row["home_team"].strip(), row["away_team"].strip())
            result[key] = {"winner": row["winner"].strip()}
    return result


def count_score_scopes(session: Session) -> dict[str, int]:
    """Count matches in each score_scope category.

    Returns:
        dict with keys "full_90min", "after_extra_time_or_unknown", "unknown_score_scope"
        and their counts.
    """
    from sqlalchemy import func

    rows = session.execute(
        select(HistoricalMatch.score_scope, func.count(HistoricalMatch.id))
        .group_by(HistoricalMatch.score_scope)
    ).all()

    counts = {
        "full_90min": 0,
        "after_extra_time_or_unknown": 0,
        "unknown_score_scope": 0,
    }
    for scope, cnt in rows:
        if scope in counts:
            counts[scope] = cnt
        else:
            logger.warning("Unexpected score_scope value: %s (%d matches)", scope, cnt)

    logger.info(
        "Score scope counts: full_90min=%d, after_extra_time_or_unknown=%d, unknown_score_scope=%d",
        counts["full_90min"], counts["after_extra_time_or_unknown"], counts["unknown_score_scope"],
    )
    return counts
