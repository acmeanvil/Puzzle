"""Warm containers (ADR 0072 section 1): inside a batch, container solves are exec'd into
one running container per image, each in its own folder, with its thread variables; a
cancelled solve is stopped inside the container; the batch's end removes it. Then a
small sweep solved warm on Code_Aster agrees with one solved cold, and is timed.

Needs Docker or Podman and the Code_Aster image (`containers/code_aster`), as the other
container tests do.
"""

import subprocess
import threading
import time

import pytest

from puzzle.jobs import warm
from puzzle.jobs.runtime import DEFAULT_IMAGES, ENGINES, ContainerRuntime, find_engine

IMAGE = DEFAULT_IMAGES["code_aster"]


def _engine() -> str | None:
    for e in ENGINES:
        if find_engine(e) and ContainerRuntime(image=IMAGE, engine=e).available("run_aster"):
            return e
    return None


ENGINE = _engine()
pytestmark = pytest.mark.skipif(ENGINE is None, reason="no container engine with the "
                                "Code_Aster image")  # fmt: skip


def _containers() -> list[str]:
    out = subprocess.run([find_engine(ENGINE), "ps", "--filter", "name=puzzle-warm-",
                          "--format", "{{.Names}}"], capture_output=True, text=True,
                         encoding="utf-8").stdout  # fmt: skip
    return [line for line in out.splitlines() if line]


def test_solves_share_one_container_in_their_own_folders(tmp_path):
    before = set(_containers())
    with warm.batch(tmp_path) as pool:
        rt = warm.warm(ContainerRuntime(image=IMAGE, engine=ENGINE))
        assert isinstance(rt, warm.WarmRuntime)
        for name in ("a", "b"):
            folder = tmp_path / name
            folder.mkdir()
            argv = rt.wrap(["sh", "-c", "pwd > where.txt; echo $OMP_NUM_THREADS >> where.txt"],
                           folder)  # fmt: skip
            assert argv[1] == "exec"
            done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
            assert done.returncode == 0, done.stderr
            lines = (folder / "where.txt").read_text(encoding="utf-8").split()
            assert lines[0] == f"/batch/{name}" and int(lines[1]) >= 1
        mine = set(_containers()) - before
        assert len(mine) == 1 and next(iter(mine)) in pool.names.values()
        # A cancel stops the solve inside: `docker exec` dying alone would leave it.
        slow = tmp_path / "slow"
        slow.mkdir()
        proc = subprocess.Popen(rt.wrap(["sleep", "120"], slow))
        deadline = time.time() + 20
        while not (slow / warm.PID).exists() and time.time() < deadline:
            time.sleep(0.2)
        started = time.time()
        threading.Timer(0.5, lambda: rt.cancel(slow)).start()
        proc.wait(timeout=60)
        assert time.time() - started < 30
    assert not (set(_containers()) - before)  # removed at the batch's end


def test_outside_a_batch_nothing_changes(tmp_path):
    rt = warm.warm(ContainerRuntime(image=IMAGE, engine=ENGINE))
    assert not isinstance(rt, warm.WarmRuntime)
    assert rt.wrap(["true"], tmp_path)[1] == "run"


def test_a_sweep_solved_warm_agrees_with_cold_and_is_timed(tmp_path, monkeypatch):
    """The cantilever's tip load over four materials on Code_Aster in its container,
    warm (one container, the default inside a sweep) and cold (`docker run` per point):
    the same tip deflections to every digit, and the time each took."""
    import contextlib
    import shutil
    from pathlib import Path

    import numpy as np

    from puzzle.materials import add_library_material
    from puzzle.model.schema import Component, Material, Study, SweepParameter, SweepStep
    from puzzle.project import load_project, save_project
    from puzzle.results.store import ResultStore
    from puzzle.solve import run_study

    example = Path(__file__).parents[2] / "examples" / "cantilever"
    monkeypatch.setenv("PUZZLE_CODE_ASTER_RUNTIME", ENGINE)

    def solve(folder: Path, warm_on: bool):
        root = folder / "cantilever"
        shutil.copytree(example, root, ignore=shutil.ignore_patterns(
            "results*", ".puzzle", "history.jsonl", "versions"))  # fmt: skip
        p = load_project(root)
        steel = next(m for m in p.of_type(Material))
        others = [add_library_material(p, k) for k in
                  ("aluminium_6061_t6", "titanium_ti6al4v", "copper")]  # fmt: skip
        comp = next(iter(p.of_type(Component)))
        study = next(s for s in p.of_type(Study) if s.name == "Tip load")
        study.solver = "code_aster"
        study.steps = [SweepStep(parameters=[SweepParameter(
            name="Bar", location=comp.materials[0].selection,
            materials=[steel.id, *(m.id for m in others)])]), *study.solve_steps()]  # fmt: skip
        save_project(p, root)
        if not warm_on:
            monkeypatch.setattr(warm, "batch", lambda _root: contextlib.nullcontext())
        store = ResultStore(root / "results")
        started = time.time()
        run_study(load_project(root), study.id, root, folder / "work", store=store,
                  reuse=False, timeout_s=1800)  # fmt: skip
        took = time.time() - started
        monkeypatch.undo()
        monkeypatch.setenv("PUZZLE_CODE_ASTER_RUNTIME", ENGINE)
        tips = sorted(float(np.abs(store.read_results(r.run_id).last("displacement").values)
                            .max()) for r in store.runs())  # fmt: skip
        runtimes = {r.runtime for r in store.runs()}
        return tips, took, runtimes

    hot, hot_s, hot_rt = solve(tmp_path / "warm", True)
    cold, cold_s, cold_rt = solve(tmp_path / "cold", False)
    print(f"4 points on Code_Aster: warm {hot_s:.1f} s, cold {cold_s:.1f} s ({hot_rt} / {cold_rt})")
    assert hot == pytest.approx(cold, rel=1e-12)
    assert any("warm" in r for r in hot_rt) and not any("warm" in r for r in cold_rt)
