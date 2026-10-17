"""Runs the real CalculiX binary through the job runner.

Skipped unless `ccx` is found natively (PUZZLE_SOLVER_PATH or PATH). The case is a
unit cube of one C3D8 element in uniaxial tension, so the answer is exact:
u_x = F L / (E A).
"""

import os
import shutil

import pytest

from puzzle.jobs import run_job, runtime_for

E = 200e9  # Pa
FORCE = 1000.0  # N, spread over the four nodes of the x = 1 face
CUBE_INP = f"""\
*NODE
1, 0, 0, 0
2, 1, 0, 0
3, 1, 1, 0
4, 0, 1, 0
5, 0, 0, 1
6, 1, 0, 1
7, 1, 1, 1
8, 0, 1, 1
*ELEMENT, TYPE=C3D8, ELSET=CUBE
1, 1, 2, 3, 4, 5, 6, 7, 8
*NSET, NSET=X0
1, 4, 5, 8
*NSET, NSET=Y0
1, 2, 5, 6
*NSET, NSET=Z0
1, 2, 3, 4
*NSET, NSET=X1
2, 3, 6, 7
*MATERIAL, NAME=STEEL
*ELASTIC
{E}, 0.3
*SOLID SECTION, ELSET=CUBE, MATERIAL=STEEL
*STEP
*STATIC
*BOUNDARY
X0, 1, 1, 0
Y0, 2, 2, 0
Z0, 3, 3, 0
*CLOAD
X1, 1, {FORCE / 4}
*NODE PRINT, NSET=X1
U
*END STEP
"""

pytestmark = pytest.mark.skipif(
    shutil.which(
        "ccx",
        path=os.pathsep.join(
            filter(None, [os.environ.get("PUZZLE_SOLVER_PATH"), os.environ.get("PATH")])
        ),
    )
    is None,
    reason="CalculiX (ccx) not installed",
)


def _displacements(dat_text: str) -> dict[int, tuple[float, float, float]]:
    rows = {}
    for line in dat_text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0].isdigit():
            rows[int(parts[0])] = tuple(float(p) for p in parts[1:])
    return rows


def test_unit_cube_tension_matches_hand_calculation(tmp_path):
    (tmp_path / "cube.inp").write_text(CUBE_INP)
    result = run_job(["ccx", "-i", "cube"], tmp_path, runtime_for("calculix"), timeout_s=120)

    assert result.ok, result.log.read_text()
    ux = {node: u[0] for node, u in _displacements((tmp_path / "cube.dat").read_text()).items()}
    assert set(ux) == {2, 3, 6, 7}
    for value in ux.values():
        assert value == pytest.approx(FORCE / E, rel=1e-6)
