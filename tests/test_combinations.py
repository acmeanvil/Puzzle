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


def _mixed_frame():
    """A portal: a stainless left column (3 m), a carbon beam (4 m) and right column; a
    10 kN point load on the beam's left end, 2 kN/m along the beam, and a 2 m × 2 m
    stainless plate (counter-clockwise about +z, PyNite's local z up) under 1 kPa down."""
    return {
        "nodes": {"1": [0, 0, 0], "2": [0, 0, 3], "3": [4, 0, 3], "4": [4, 0, 0],
                  "5": [10, 0, 3], "6": [12, 0, 3], "7": [12, 2, 3], "8": [10, 2, 3]},
        "members": [{"name": "C1", "i": "1", "j": "2", "material": "ss"},
                    {"name": "B", "i": "2", "j": "3", "material": "cs"},
                    {"name": "C2", "i": "3", "j": "4", "material": "cs"}],
        "quads": [{"name": "Q1", "nodes": ["5", "6", "7", "8"], "material": "ss"}],
        "point_loads": [{"node": "2", "direction": "FZ", "P": -10e3, "case": "D"}],
        "line_loads": [{"member": "B", "direction": "FZ", "w": -2e3, "case": "D"}],
        "pressures": [{"quad": "Q1", "p": -1e3, "case": "D"}],
        "combos": [{"name": "1.2D", "factors": {"D": 1.2}}],
    }  # fmt: skip


def _notionals(fractions):
    from puzzle.adapters.pynite.case import _notional

    case = _mixed_frame()
    _notional(case, {"notional_directions": "+x"}, fractions)
    (combo,) = case["combos"]
    return {p["node"]: p["P"] for p in case["point_loads"] if p["case"] in combo["factors"]
            and p["case"] != "D"}  # fmt: skip


def test_direct_analysis_notional_loads_by_each_members_material():
    """AISC 360 C2.2b N_i = 0.002 Y_i with C2.3(c)'s 0.001 Y_i for τ_b = 1 throughout
    (0.003; ADR 0090 PYN-2), AISC 370 C2.3(c) 0.004 Y_i on stainless, Y_i the factored
    gravity (1.2 D): the beam's line load (carbon) at 0.003 of its 4 kN per end; the
    point load at node 2 at the length-weighted mean of the members there,
    (3 × 0.004 + 4 × 0.003) / 7; the plate's 4 kN (1 kPa × 4 m², a quarter per corner)
    at 0.004. All carbon: 0.003 everywhere."""
    got = _notionals({"ss": 0.004})
    at_2 = (3 * 0.004 + 4 * 0.003) / 7
    assert got["2"] == pytest.approx(1.2 * (10e3 * at_2 + 4e3 * 0.003))
    assert got["3"] == pytest.approx(1.2 * 4e3 * 0.003)
    for n in "5678":
        assert got[n] == pytest.approx(1.2 * 1e3 * 0.004)
    carbon = _notionals({})
    assert carbon == pytest.approx({"2": 1.2 * 14e3 * 0.003, "3": 1.2 * 4e3 * 0.003,
                                    **{n: 1.2 * 1e3 * 0.003 for n in "5678"}})  # fmt: skip
