"""CalculiX's .frd and .dat read as CalculiX writes them (ADR 0090 package 6).

CCX-1: the `100CL` header is fixed width; from the 9,900th output increment its counter
touches the tag (`100CL10000`) and a negative value touches the counter. Both blocks
must be read with their own value.
CCX-3: .dat prints (reaction totals, fastener forces) pair with .frd steps by order in a
deck step; the .frd prints a time to six digits and the .dat to seven, so dt = 1/30 s
matched nothing by an absolute 1e-9.
"""

from pathlib import Path

import numpy as np
import pytest

from puzzle.adapters.calculix import matched
from puzzle.adapters.calculix.frd import read_frd
from puzzle.adapters.calculix.inp import read_totals


def _frd(blocks: list[tuple[int, int, str]]) -> str:
    """A .frd of two nodes, one DISP block per (deck step, output increment, value as
    CalculiX prints it, 12 wide)."""
    lines = ["    1C", "    2C                             2                                     1",
             " -1         1 0.00000E+00 0.00000E+00 0.00000E+00",
             " -1         2 1.00000E+00 0.00000E+00 0.00000E+00", " -3"]  # fmt: skip
    for k, (step, inc, value) in enumerate(blocks, start=1):
        lines += [
            f"    1PSTEP{k:26d}{inc:12d}{step:12d}",
            f"  100CL{100 + inc:5d}{value:>12s}{2:12d}{1:22d}{inc:5d}{1:12d}",
            " -4  DISP        4    1",
            " -5  D1          1    2    1    0",
            " -5  D2          1    2    2    0",
            " -5  D3          1    2    3    0",
            " -5  ALL         1    2    0    0    1ALL",
            f" -1         1{inc * 1e-6:12.5E} 0.00000E+00 0.00000E+00",
            f" -1         2{-inc * 1e-6:12.5E} 0.00000E+00 0.00000E+00",
            " -3",
        ]
    return "\n".join(lines + ["9999", ""])


def test_frd_blocks_past_9899_increments_are_read(tmp_path):
    """CCX-1: the 9,899th, 9,900th and 10,050th output increments all come back, each
    with its own time and displacement (CalculiX wrote 10,050; 9,899 were read)."""
    blocks = [(1, 9899, "9.89900E-02"), (1, 9900, "9.90000E-02"), (1, 10050, "1.00500E-01")]
    path = tmp_path / "a.frd"
    path.write_text(_frd(blocks), encoding="utf-8")
    steps = read_frd(path).steps
    assert [s.value for s in steps] == pytest.approx([0.09899, 0.099, 0.1005], rel=1e-12)
    assert [s.index for s in steps] == [1, 1, 1]
    u = [s.fields["displacement"].values[0, 0] for s in steps]
    assert u == pytest.approx([9899e-6, 9900e-6, 10050e-6], rel=1e-5)


def test_frd_negative_value_touching_the_counter(tmp_path):
    """CCX-1, to check: a negative 12-wide value (a buckling factor) touches the counter;
    split on spaces it read the node count as the value."""
    path = tmp_path / "b.frd"
    path.write_text(_frd([(2, 3, "-1.08870E+10"), (2, 10001, "-2.50000E+03")]), encoding="utf-8")
    steps = read_frd(path).steps
    assert [s.value for s in steps] == pytest.approx([-1.0887e10, -2500.0], rel=1e-12)
    assert [s.index for s in steps] == [2, 2]


DAT = """
                        S T E P       1


                                INCREMENT     1


 total force (fx,fy,fz) for set BOT and time  0.3333333E-02

       -6.479746E+00 -2.220446E-16  0.000000E+00

                                INCREMENT     2


 total force (fx,fy,fz) for set BOT and time  0.6666667E-02

       -1.898174E+00  2.775558E-17  4.440892E-16

                                INCREMENT     3


 total force (fx,fy,fz) for set BOT and time  0.1000000E-01

       -5.543080E+00 -1.665335E-16  4.440892E-16
"""  # CalculiX 2.23's .dat for *DYNAMIC 1/300 s to 0.01 s, recorded


def test_dat_prints_pair_with_frd_steps_at_seven_digit_times(tmp_path):
    """CCX-3: each of the three increments of 1/300 s gets its total (two had none)."""
    path = tmp_path / "e.frd"
    path.write_text(_frd([(1, 1, "3.33333E-03"), (1, 2, "6.66667E-03"), (1, 3, "1.00000E-02")]),
                    encoding="utf-8")  # fmt: skip
    steps = read_frd(path).steps
    pairs = matched(steps, read_totals(DAT))
    assert sorted(pairs) == [0, 1, 2]
    assert [pairs[k]["BOT"][0] for k in range(3)] == [-6.479746, -1.898174, -5.543080]


def test_dat_prints_pair_by_nearest_time_when_counts_differ(tmp_path):
    """Thinned .frd output with every increment printed (an older deck): the nearest
    time within the .frd's rounding, never a neighbour's block."""
    path = tmp_path / "e.frd"
    path.write_text(_frd([(1, 2, "6.66667E-03")]), encoding="utf-8")
    pairs = matched(read_frd(path).steps, read_totals(DAT))
    assert pairs[0]["BOT"][0] == -1.898174
    assert matched(read_frd(path).steps, [(1, 6.6e-3, {"BOT": np.zeros(3)})]) == {}


def test_recorded_frd_still_reads_as_before():
    steps = read_frd(Path(__file__).parent / "data" / "cube_static_modal.frd").steps
    assert [(s.index, s.mode) for s in steps] == [(1, None), (2, 1), (2, 2)]
    assert steps[1].value == pytest.approx(636.8916146)


def test_plastic_strain_tensor_is_zero_off_plastic_bodies():
    """COMMON-2: in a model of a plastic and an elastic material, the elastic body's
    nodes got NaN components (E and nu were filled for plastic materials only); they
    have no plastic strain, as Code_Aster's EPSP says. Node 1: E = 200 GPa, nu = 0.3,
    uniaxial 200 MPa and 2e-3 strain in x: ep_xx = 2e-3 - 1e-3 = 1e-3, ep_yy = ep_zz =
    -0.3e-3 + 0.3e-3 = 0 from the given lateral strain of -0.3e-3."""
    from puzzle.adapters.common import plastic_strain_tensor
    from puzzle.results.model import Field

    comps = ["xx", "yy", "zz", "xy", "yz", "zx"]
    ids = np.array([1, 2])
    strain = Field(
        "strain",
        "node",
        comps,
        ids,
        np.array([[2e-3, -0.3e-3, -0.3e-3, 0, 0, 0], [1e-3, 0, 0, 0, 0, 0]]),
    )
    stress = Field("stress", "node", comps, ids,
                   np.array([[200e6, 0, 0, 0, 0, 0], [70e6, 0, 0, 0, 0, 0]]))  # fmt: skip
    ep = plastic_strain_tensor(strain, stress, [(200e9, 0.3, [1])]).values
    assert not np.isnan(ep).any()
    assert ep[0] == pytest.approx([1e-3, 0, 0, 0, 0, 0], abs=1e-12)
    assert ep[1] == pytest.approx(np.zeros(6))


def test_deck_loads_follow_calculix_steps():
    """CCX-11's loads at a result's time, as CalculiX applies them (measured on a
    one-spring deck): a *DYNAMIC step's load whole from its first increment, a *STATIC
    step's ramped from the value before, OP=NEW clearing, an AMPLITUDE in step time."""
    from puzzle.adapters.calculix.spring_beams import deck_loads

    deck = """*NSET, NSET=N_TIP
2, 3
*AMPLITUDE, NAME=A1
0., 0., 1., 4.
*STEP
*DYNAMIC, DIRECT
*CLOAD
N_TIP, 1, 1.
*END STEP
*STEP
*STATIC
*CLOAD
2, 1, 3.
*END STEP
*STEP
*STATIC
*CLOAD, OP=NEW
3, 2, 2., 
*CLOAD, AMPLITUDE=A1
2, 3, 5.
*END STEP
"""
    loads = deck_loads(deck, {2, 3})
    assert loads.at(1, 0.25, 0.25)[2][0] == 1.0 and loads.at(1, 0.25, 0.25)[3][0] == 1.0
    assert loads.at(2, 0.5, 0.5)[2][0] == pytest.approx(2.0)  # 1 -> 3, halfway
    third = loads.at(3, 0.5, 0.5)
    assert third[2][0] == pytest.approx(1.5) and third[3][0] == pytest.approx(0.5)  # removed
    assert third[3][1] == pytest.approx(1.0) and third[2][2] == pytest.approx(10.0)
