"""ASCE 7-16's load combinations (ADR 0079 step 2) from the cases a frame has."""

import pytest

from puzzle.frames.combinations import asce7, notional


def test_lrfd_with_dead_live_snow_and_wind():
    got = dict(asce7(["D", "L", "S", "W"]))
    assert got["1.4D"] == {"D": 1.4}
    assert got["1.2D + 1.6L + 0.5S"] == {"D": 1.2, "L": 1.6, "S": 0.5}
    assert got["1.2D + L + 1.6S"] == {"D": 1.2, "L": 1.0, "S": 1.6}
    assert got["1.2D + L + 0.5S + W"] == {"D": 1.2, "L": 1.0, "S": 0.5, "W": 1.0}
    assert got["1.2D + L + 0.5S - W"]["W"] == -1.0  # wind both ways
    assert got["0.9D + W"] == {"D": 0.9, "W": 1.0} and "0.9D - W" in got
    assert not any("E" in c for c in got.values())  # no seismic case: no seismic combination
    assert len(got) == 9


def test_asd_with_seismic():
    got = dict(asce7(["D", "L", "E"], "ASD"))
    assert got["D + 0.75L + 0.525E"] == {"D": 1.0, "L": 0.75, "E": 0.525}  # 0.75 (0.7E)
    assert got["0.6D - 0.7E"] == {"D": 0.6, "E": -0.7}
    assert "D + Lr" not in got and not any(k.startswith("D + 0.75L + 0.75") for k in got)


def test_one_combination_per_roof_load():
    got = dict(asce7(["D", "L", "Lr", "S"]))
    assert "1.2D + 1.6L + 0.5Lr" in got and "1.2D + 1.6L + 0.5S" in got
    assert "1.2D + L + 1.6Lr" in got and "1.2D + L + 1.6S" in got


def test_dead_load_is_needed():
    with pytest.raises(ValueError, match="dead load"):
        asce7(["L"])


def test_notional_loads_are_two_thousandths_of_gravity():
    assert notional({"1": 1000.0, "2": 250.0}) == {"1": 2.0, "2": 0.5}
