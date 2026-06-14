from datetime import datetime, timezone

from app.providers.worldcup26 import WorldCup26Provider


def test_normalize_match_uses_team_mapping_and_group_date():
    teams = {
        "15": WorldCup26Provider._normalize_team({
            "id": "15",
            "name_en": "Australia",
            "name_fa": "استرالیا",
            "fifa_code": "AUS",
            "groups": "D",
            "flag": "https://flagcdn.com/w80/au.png",
        }),
        "16": WorldCup26Provider._normalize_team({
            "id": "16",
            "name_en": "Turkey",
            "name_fa": "ترکیه",
            "fifa_code": "TUR",
            "groups": "D",
            "flag": "https://flagcdn.com/w80/tr.png",
        }),
    }

    match = WorldCup26Provider._normalize_match(
        {
            "id": "8",
            "home_team_id": "15",
            "away_team_id": "16",
            "home_score": "2",
            "away_score": "0",
            "group": "D",
            "local_date": "06/14/2026 09:00",
            "finished": "TRUE",
            "stadium_id": "13",
        },
        teams,
        {"13": "BC Place"},
    )

    assert match.id == "2026-D-AUS-TUR-2026-06-14"
    assert match.status == "final"
    assert (match.home_score, match.away_score) == (2, 0)
    assert match.venue == "BC Place"
    assert match.kickoff == datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc)


def test_normalize_builds_worldcup26_payload():
    teams = [
        {
            "id": str(index + 1),
            "name_en": f"Team {index}",
            "name_fa": f"Team FA {index}",
            "fifa_code": code,
            "groups": group,
            "flag": f"https://flags.example/{code.lower()}.png",
        }
        for group, codes in {
            "A": ["MEX", "RSA", "KOR", "CZE"],
            "B": ["CAN", "BIH", "PER", "CMR"],
            "C": ["HAI", "SCO", "BRA", "MAR"],
            "D": ["USA", "PAR", "AUS", "TUR"],
            "E": ["GER", "CUW", "CIV", "ECU"],
            "F": ["NED", "JPN", "SWE", "TUN"],
            "G": ["BEL", "EGY", "IRN", "NZL"],
            "H": ["ESP", "CPV", "KSA", "URU"],
            "I": ["FRA", "SEN", "IRQ", "NOR"],
            "J": ["ARG", "ALG", "AUT", "JOR"],
            "K": ["ENG", "VEN", "CHN", "UZB"],
            "L": ["POR", "GHA", "COL", "GRE"],
        }.items()
        for index, code in enumerate(codes, start=(ord(group) - ord("A")) * 4)
    ]
    team_ids = {team["fifa_code"]: team["id"] for team in teams}
    stadiums = [{"id": str(index), "name_en": f"Stadium {index}"} for index in range(1, 17)]
    games = []
    pairings = [
        ("A", [("MEX", "RSA"), ("KOR", "CZE"), ("MEX", "KOR"), ("RSA", "CZE"), ("MEX", "CZE"), ("RSA", "KOR")]),
        ("B", [("CAN", "BIH"), ("PER", "CMR"), ("CAN", "PER"), ("BIH", "CMR"), ("CAN", "CMR"), ("BIH", "PER")]),
        ("C", [("HAI", "SCO"), ("BRA", "MAR"), ("HAI", "BRA"), ("SCO", "MAR"), ("HAI", "MAR"), ("SCO", "BRA")]),
        ("D", [("USA", "PAR"), ("AUS", "TUR"), ("USA", "AUS"), ("PAR", "TUR"), ("USA", "TUR"), ("PAR", "AUS")]),
        ("E", [("GER", "CUW"), ("CIV", "ECU"), ("GER", "CIV"), ("CUW", "ECU"), ("GER", "ECU"), ("CUW", "CIV")]),
        ("F", [("NED", "JPN"), ("SWE", "TUN"), ("NED", "SWE"), ("JPN", "TUN"), ("NED", "TUN"), ("JPN", "SWE")]),
        ("G", [("BEL", "EGY"), ("IRN", "NZL"), ("BEL", "IRN"), ("EGY", "NZL"), ("BEL", "NZL"), ("EGY", "IRN")]),
        ("H", [("ESP", "CPV"), ("KSA", "URU"), ("ESP", "KSA"), ("CPV", "URU"), ("ESP", "URU"), ("CPV", "KSA")]),
        ("I", [("FRA", "SEN"), ("IRQ", "NOR"), ("FRA", "IRQ"), ("SEN", "NOR"), ("FRA", "NOR"), ("SEN", "IRQ")]),
        ("J", [("ARG", "ALG"), ("AUT", "JOR"), ("ARG", "AUT"), ("ALG", "JOR"), ("ARG", "JOR"), ("ALG", "AUT")]),
        ("K", [("ENG", "VEN"), ("CHN", "UZB"), ("ENG", "CHN"), ("VEN", "UZB"), ("ENG", "UZB"), ("VEN", "CHN")]),
        ("L", [("POR", "GHA"), ("COL", "GRE"), ("POR", "COL"), ("GHA", "GRE"), ("POR", "GRE"), ("GHA", "COL")]),
    ]
    day = 11
    game_id = 1
    for group, fixtures in pairings:
        for home, away in fixtures:
            games.append({
                "id": str(game_id),
                "home_team_id": team_ids[home],
                "away_team_id": team_ids[away],
                "home_score": "0",
                "away_score": "0",
                "group": group,
                "local_date": f"06/{day:02d}/2026 12:00",
                "finished": "FALSE",
                "stadium_id": "1",
                "type": "group",
            })
            game_id += 1
            day = 11 + (game_id % 18)

    payload = WorldCup26Provider._normalize(
        raw_games=games,
        raw_teams=teams,
        raw_stadiums=stadiums,
        fetched_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )

    assert payload.source.provider == "worldcup26"
    assert len(payload.teams) == 48
    assert len(payload.matches) == 72
