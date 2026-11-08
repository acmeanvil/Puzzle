"""Exact reaction totals: CalculiX prints each selection's total itself."""

import pytest


def test_reaction_totals_are_read_from_calculixs_own_print():
    """CalculiX prints each selection's reaction total (TOTALS=ONLY) exactly; the .frd's
    nodal reactions have 6 digits, whose rounding summed reached 2e-5 of a total."""
    import numpy as np

    from puzzle.adapters.calculix.inp import read_totals
    from puzzle.results.model import Field, StepResult
    from puzzle.results.query import exact_total, selection_id

    dat = """
                        S T E P       1


                                INCREMENT     1


 total force (fx,fy,fz) for set N_BAR_ROOT and time  0.1000000E+01

       -1.312321E-01  1.700000E-11  1.496888E+00

 total force (fx,fy,fz) for set N_BAR_TIP and time  0.1000000E+01

        1.050000E+01 -3.250000E+00  1.000000E+02
"""
    [(step, time, sets)] = read_totals(dat)
    assert (step, time) == (1, 1.0)
    assert sets["N_BAR_ROOT"] == pytest.approx([-0.1312321, 1.7e-11, 1.496888])
    field = Field("reaction_total", "global", ["x", "y", "z"],
                  np.array([selection_id("Bar/Root")]), np.array([sets["N_BAR_ROOT"]]))  # fmt: skip
    s = StepResult(1, "stationary", 1.0, fields={"reaction_total": field})
    assert exact_total(s, "Bar/Root") == pytest.approx([-0.1312321, 1.7e-11, 1.496888])
    assert exact_total(s, "Bar/Tip") is None  # not printed: the nodal sum stands
