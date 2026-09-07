"""Superflex (Q/W/R/T) slot support."""

from app.services import evaluator


def test_superflex_alias_normalizes():
    assert evaluator.normalize_slot("Q/W/R/T") == "SUPERFLEX"
    assert evaluator.normalize_slot("op") == "SUPERFLEX"


def test_position_filter_expands_superflex():
    assert evaluator.position_filter("Q/W/R/T") == ["QB", "RB", "WR", "TE"]


def test_starter_slots_distributes_superflex():
    slots = evaluator.starter_slots({"QB": 1, "RB": 2, "Q/W/R/T": 1, "BN": 8})
    assert slots["QB"] == 1.75
    assert slots["RB"] == 2.1
    assert slots["WR"] == 0.1
    assert slots["TE"] == 0.05


def test_slot_instances_include_superflex_after_flex():
    instances = evaluator.slot_instances(
        {"QB": 1, "RB": 1, "FLEX": 1, "Q/W/R/T": 1, "BN": 2}
    )
    assert instances == ["QB", "RB", "FLEX", "SUPERFLEX"]


def test_fill_lineup_superflex_takes_best_remaining_qb():
    roster_slots = {"QB": 1, "RB": 1, "FLEX": 1, "SUPERFLEX": 1}
    points = {1: 300.0, 2: 250.0, 3: 200.0, 4: 150.0, 5: 100.0}
    positions = {1: "QB", 2: "QB", 3: "RB", 4: "RB", 5: "WR"}

    lineup = evaluator._fill_lineup(roster_slots, points, positions, [1, 2, 3, 4, 5])

    assert ("QB", 1) in lineup
    assert ("RB", 3) in lineup
    # FLEX cannot take a QB, so it takes the next-best RB/WR/TE...
    assert ("FLEX", 4) in lineup
    # ...and SUPERFLEX takes the remaining (better) QB.
    assert ("SUPERFLEX", 2) in lineup
