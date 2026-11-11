"""Which solver ran is kept with each run (ADR 0072): a native CalculiX's version from
its environment's conda-meta, a PARDISO build named as built here, a container's from
its image; the store keeps both columns, older stores reading them as empty."""

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from puzzle.jobs import versions


def test_a_native_solver_is_named_from_its_package(tmp_path, monkeypatch):
    env = tmp_path / "envs" / "calculix"
    (env / "conda-meta").mkdir(parents=True)
    (env / "conda-meta" / "calculix-2.23-h1.json").write_text(
        json.dumps({"name": "calculix", "version": "2.23"}), encoding="utf-8"
    )
    exe = env / "bin" / "ccx"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    from puzzle.jobs import runtime

    monkeypatch.setattr("shutil.which", lambda name, path=None: str(exe))
    rt = runtime.NativeRuntime()
    assert versions._native("calculix", rt) == "2.23"
    built = tmp_path / "envs" / "calculix-pardiso"
    (built / "conda-meta").mkdir(parents=True)
    (built / "bin").mkdir()
    ccx = built / "bin" / "ccx"
    ccx.write_text("", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name, path=None: str(ccx))
    assert versions._native("calculix", rt).endswith("(PARDISO, built here)")


def test_identify_never_raises(monkeypatch):
    from puzzle.jobs import runtime

    monkeypatch.setattr(runtime, "runtime_for", lambda solver: (_ for _ in ()).throw(OSError))
    versions._SEEN.clear()
    assert versions.identify("calculix") == (None, None)


def test_the_store_keeps_them(tmp_path):
    from puzzle.mesh import Mesh
    from puzzle.mesh.mesh import CellBlock
    from puzzle.results.model import Results
    from puzzle.results.store import ResultStore

    tet = CellBlock("tet4", 3, (3, 1), np.array([1]), np.array([[1, 2, 3, 4]]))
    mesh = Mesh(np.arange(1, 5), np.eye(4, 3), [tet], {})
    store = ResultStore(Path(tmp_path) / "results")
    run = store.write_run(run_key="k", study_id="s", study_name="S", solver="calculix",
                          runtime="native", mesh=mesh, results=Results("calculix", []),
                          started_at=datetime.now(UTC), duration_s=1.0,
                          solver_version="2.23", image_digest=None)  # fmt: skip
    record = store.get_run(run)
    assert record.solver_version == "2.23" and record.image_digest is None
