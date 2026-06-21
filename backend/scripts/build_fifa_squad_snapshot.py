from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PDF_PATH = ROOT / "data" / "external" / "fifa" / "SquadLists-English-2026-06-20.pdf"
OUT_PATH = ROOT / "data" / "seed" / "world-cup-2026-squads.json"
SOURCE_URL = "https://fdp.fifa.org/assetspublic/ce281/pdf/SquadLists-English.pdf"


def _parse_team_header(text: str) -> tuple[str, str]:
    for line in text.splitlines():
        match = re.fullmatch(r"(.+) \(([A-Z]{3})\)", line.strip())
        if match:
            return match.group(1), match.group(2)
    raise ValueError("team header not found")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("\x00", "").strip()


def _player_from_row(row: list[str | None]) -> dict | None:
    if not row or row[0] == "#" or row[1] not in {"GK", "DF", "MF", "FW"}:
        return None
    try:
        dob_idx = next(idx for idx, value in enumerate(row) if value and re.fullmatch(r"\d{2}/\d{2}/\d{4}", value))
        non_empty_tail = [value for value in row[dob_idx + 1:] if value]
        club_values = non_empty_tail[:-3]
        height, caps, goals = non_empty_tail[-3:]
        shirt_name = next(value for value in reversed(row[:dob_idx]) if value)
        return {
            "number": int(row[0]),
            "position": row[1],
            "player_name": _clean(row[2]),
            "first_names": _clean(row[4]),
            "last_names": _clean(row[5]),
            "shirt_name": _clean(shirt_name),
            "date_of_birth": datetime.strptime(row[dob_idx], "%d/%m/%Y").date().isoformat(),
            "club": _clean(" ".join(club_values)),
            "height_cm": int(height) if height else None,
            "caps": int(caps) if caps else 0,
            "goals": int(goals) if goals else 0,
        }
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"cannot parse player row: {row}") from exc


def _coach_from_rows(rows: list[list[str | None]]) -> dict | None:
    for row in rows:
        if row and row[0] == "Head coach":
            return {
                "role": row[0],
                "name": row[1],
                "first_names": row[2],
                "last_names": row[3],
                "nationality": row[4],
            }
    return None


def _summarize(players: list[dict]) -> dict:
    positions = Counter(player["position"] for player in players)
    total_caps = sum(player["caps"] for player in players)
    total_goals = sum(player["goals"] for player in players)
    top_scorers = sorted(players, key=lambda item: (-item["goals"], -item["caps"], item["number"]))[:3]
    most_capped = sorted(players, key=lambda item: (-item["caps"], -item["goals"], item["number"]))[:3]
    return {
        "squad_size": len(players),
        "position_counts": dict(sorted(positions.items())),
        "total_caps": total_caps,
        "total_goals": total_goals,
        "average_caps": round(total_caps / len(players), 1) if players else None,
        "average_height_cm": round(sum(player["height_cm"] or 0 for player in players) / len(players), 1) if players else None,
        "top_scorers_in_squad": [
            {
                "name": player["player_name"],
                "shirt_name": player["shirt_name"],
                "position": player["position"],
                "caps": player["caps"],
                "goals": player["goals"],
                "club": player["club"],
            }
            for player in top_scorers
        ],
        "most_capped_players": [
            {
                "name": player["player_name"],
                "shirt_name": player["shirt_name"],
                "position": player["position"],
                "caps": player["caps"],
                "goals": player["goals"],
                "club": player["club"],
            }
            for player in most_capped
        ],
    }


def build_snapshot(pdf_path: Path = PDF_PATH) -> dict:
    try:
        import pdfplumber
    except ImportError as exc:
        raise SystemExit(
            "pdfplumber is required. Use the Codex workspace Python runtime or install pdfplumber."
        ) from exc

    teams = {}
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            team_name, team_code = _parse_team_header(text)
            tables = page.extract_tables()
            if not tables:
                raise ValueError(f"no table found for {team_code}")
            rows = tables[0]
            players = [player for row in rows if (player := _player_from_row(row))]
            if len(players) != 26:
                raise ValueError(f"{team_code} has {len(players)} parsed players, expected 26")
            teams[team_code] = {
                "team_name": team_name,
                "team_code": team_code,
                "coach": _coach_from_rows(rows),
                "players": players,
                "summary": _summarize(players),
            }
    return {
        "source": {
            "provider": "fifa_official_squad_list",
            "source_url": SOURCE_URL,
            "source_file": str(pdf_path.relative_to(ROOT)),
            "published_at_utc": "2026-06-20T05:19:00Z",
            "version": "1",
            "scope": "official tournament squad list; not live injury, suspension, or starting lineup status",
        },
        "coverage": {
            "team_count": len(teams),
            "players_per_team": 26,
            "player_count": sum(len(team["players"]) for team in teams.values()),
        },
        "teams": dict(sorted(teams.items())),
    }


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else PDF_PATH
    snapshot = build_snapshot(path)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({
        "teams": snapshot["coverage"]["team_count"],
        "players": snapshot["coverage"]["player_count"],
        "output": str(OUT_PATH),
    })


if __name__ == "__main__":
    main()
