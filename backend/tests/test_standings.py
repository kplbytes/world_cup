from app.domain.standings import MatchResult, StandingRow, rank_group, rank_third_placed


def test_group_table_calculates_points_and_orders_the_group():
    matches = [
        MatchResult("A", "C", 2, 0),
        MatchResult("B", "D", 1, 0),
        MatchResult("A", "D", 1, 0),
        MatchResult("B", "C", 0, 0),
        MatchResult("A", "B", 0, 1),
        MatchResult("C", "D", 1, 0),
    ]

    table = rank_group(["A", "B", "C", "D"], matches)

    assert [row.team_id for row in table] == ["B", "A", "C", "D"]
    assert (table[0].played, table[0].won, table[0].drawn, table[0].lost) == (3, 2, 1, 0)
    assert (table[0].goals_for, table[0].goals_against, table[0].points) == (2, 0, 7)


def test_tied_teams_are_reordered_by_head_to_head_result():
    matches = [
        MatchResult("A", "B", 2, 0),
        MatchResult("C", "D", 1, 0),
        MatchResult("A", "C", 0, 1),
        MatchResult("B", "D", 3, 0),
        MatchResult("A", "D", 1, 1),
        MatchResult("B", "C", 0, 0),
    ]

    table = rank_group(["A", "B", "C", "D"], matches)

    assert [row.team_id for row in table] == ["C", "A", "B", "D"]
    assert table[1].points == table[2].points == 4
    assert table[1].goal_difference == table[2].goal_difference == 1
    assert table[1].goals_for == table[2].goals_for == 3


def test_unresolved_tie_is_deterministic_and_marked_uncertain():
    table = rank_group(["A", "B", "C", "D"], [])

    assert [row.team_id for row in table] == ["A", "B", "C", "D"]
    assert all(row.tiebreak_uncertain for row in table)


def test_best_eight_third_placed_teams_advance():
    rows = [
        StandingRow(team_id=f"{letter}3", points=12 - index, goals_for=index)
        for index, letter in enumerate("ABCDEFGHIJKL")
    ]

    ranked = rank_third_placed(rows)

    assert len(ranked.qualified) == 8
    assert ranked.qualified[-1].team_id == "H3"
    assert [row.team_id for row in ranked.eliminated] == ["I3", "J3", "K3", "L3"]

