from scripts.populate_fifa_rankings import parse_official_fifa_rankings


def test_parse_official_fifa_rankings_uses_pubdate_rank_and_decimal_points():
    payload = {
        "Results": [
            {
                "IdCountry": "ARG",
                "Rank": 1,
                "DecimalTotalPoints": 1877.27,
                "PubDate": "2026-06-11T10:00:00+00:00",
            },
            {
                "IdCountry": "FRA",
                "Rank": 2,
                "TotalPoints": 1870,
                "PubDate": "2026-06-11T10:00:00+00:00",
            },
            {"IdCountry": "BAD", "Rank": None, "DecimalTotalPoints": 0},
        ]
    }

    parsed = parse_official_fifa_rankings(payload)

    assert parsed["latest_date"] == "2026-06-11"
    assert parsed["rankings"]["ARG"] == {"rank": 1, "points": 1877.27}
    assert parsed["rankings"]["FRA"] == {"rank": 2, "points": 1870.0}
    assert "BAD" not in parsed["rankings"]
