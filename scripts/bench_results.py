"""Results storage benchmark for ADR 0008: Delta Lake store against VTKHDF.

    python scripts/bench_results.py --nodes 100000 --steps 1 10 100

Builds a structured hex mesh of about `--nodes` nodes and smooth displacement (3) and
stress (6) nodal fields at each step, then for each storage measures:

- size on disk (MB) and write time (s);
- first frame (s): open the mesh and one field at the last step, as a viewer does;
- full read (s): every field at every step.

Prints a Markdown table. Smooth fields stand in for real results (random numbers would
not compress like them). Needs VTK >= 9.4 for the HDF writer.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from puzzle.mesh.mesh import CellBlock, Group, Mesh
from puzzle.results.model import Field, Results, StepResult
from puzzle.results.store import ResultStore

HEX_CORNERS = [
    (0, 0, 0),
    (1, 0, 0),
    (1, 1, 0),
    (0, 1, 0),
    (0, 0, 1),
    (1, 0, 1),
    (1, 1, 1),
    (0, 1, 1),
]


def hex_mesh(nodes: int) -> Mesh:
    n = max(2, round(nodes ** (1 / 3)) - 1)  # cells per side
    g = np.linspace(0.0, 1.0, n + 1)
    x, y, z = np.meshgrid(g, g, g, indexing="ij")
    coords = np.column_stack([x.ravel(), y.ravel(), z.ravel()])
    ids = np.arange(1, len(coords) + 1)

    def node(i, j, k):
        return (i * (n + 1) + j) * (n + 1) + k + 1

    i, j, k = (a.ravel() for a in np.meshgrid(*(np.arange(n),) * 3, indexing="ij"))
    conn = np.column_stack([node(i + a, j + b, k + c) for a, b, c in HEX_CORNERS])
    mesh = Mesh(node_ids=ids, coords=coords)
    mesh.blocks.append(CellBlock("hex8", 3, (3, 1), np.arange(1, len(conn) + 1), conn))
    mesh.groups["All"] = Group("All", 3, [(3, 1)], ids)
    return mesh


def results(mesh: Mesh, steps: int) -> Results:
    x, y, z = mesh.coords.T
    out = []
    for s in range(1, steps + 1):
        t = s / steps
        u = np.column_stack([np.sin(3 * x) * t, np.cos(2 * y) * t, x * y * z * t]) * 1e-3
        stress = np.column_stack([np.sin(k * x + y) * t * 1e8 for k in range(1, 7)])
        out.append(
            StepResult(
                1,
                "transient",
                t,
                fields={
                    "displacement": Field(
                        "displacement", "node", ["x", "y", "z"], mesh.node_ids, u
                    ),
                    "stress": Field(
                        "stress",
                        "node",
                        ["xx", "yy", "zz", "xy", "yz", "zx"],
                        mesh.node_ids,
                        stress,
                    ),
                },
            )
        )
    return Results("bench", out)


def size_mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1e6
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def bench_delta(mesh, res, root: Path) -> dict:
    store = ResultStore(root)
    t0 = time.perf_counter()
    run = store.write_run(
        run_key="k",
        study_id="s",
        study_name="s",
        solver="bench",
        runtime="none",
        mesh=mesh,
        results=res,
        started_at=datetime.now(UTC),
        duration_s=0.0,
    )
    write = time.perf_counter() - t0
    fresh = ResultStore(root)
    t0 = time.perf_counter()
    m = fresh.read_mesh(fresh.get_run(run).mesh_id)
    fresh.read_field(run, "displacement")
    first = time.perf_counter() - t0
    del m
    t0 = time.perf_counter()
    fresh.read_results(run)
    full = time.perf_counter() - t0
    return {"size": size_mb(root), "write": write, "first": first, "full": full}


def _grid(mesh: Mesh):
    import pyvista as pv

    (block,) = mesh.blocks
    cells = np.hstack([np.full((len(block.ids), 1), 8), block.connectivity - 1]).ravel()
    return pv.UnstructuredGrid(cells, np.full(len(block.ids), 12, np.uint8), mesh.coords)


def bench_vtkhdf(mesh, res, path: Path) -> dict:
    import vtk
    from vtkmodules.util import numpy_support as ns
    from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase

    grid = _grid(mesh)
    times = [s.value for s in res.steps]
    sddp = vtk.vtkStreamingDemandDrivenPipeline

    class Steps(VTKPythonAlgorithmBase):
        def __init__(self):
            super().__init__(nInputPorts=0, nOutputPorts=1, outputType="vtkUnstructuredGrid")

        def RequestInformation(self, request, inInfo, outInfo):
            info = outInfo.GetInformationObject(0)
            info.Set(sddp.TIME_STEPS(), times, len(times))
            info.Set(sddp.TIME_RANGE(), [times[0], times[-1]], 2)
            return 1

        def RequestData(self, request, inInfo, outInfo):
            info = outInfo.GetInformationObject(0)
            t = info.Get(sddp.UPDATE_TIME_STEP()) if info.Has(sddp.UPDATE_TIME_STEP()) else 0.0
            step = res.steps[int(np.argmin(np.abs(np.asarray(times) - t)))]
            out = vtk.vtkUnstructuredGrid.GetData(outInfo)
            out.ShallowCopy(grid)
            for f in step.fields.values():
                a = ns.numpy_to_vtk(np.ascontiguousarray(f.values), deep=True)
                a.SetName(f.name)
                out.GetPointData().AddArray(a)
            return 1

    source = Steps()  # keep a reference: VTK holds only its output port
    writer = vtk.vtkHDFWriter()
    writer.SetInputConnection(source.GetOutputPort())
    writer.SetFileName(str(path))
    writer.SetWriteAllTimeSteps(len(times) > 1)
    writer.SetCompressionLevel(4)
    t0 = time.perf_counter()
    writer.Write()
    write = time.perf_counter() - t0

    t0 = time.perf_counter()
    reader = vtk.vtkHDFReader()
    reader.SetFileName(str(path))
    reader.UpdateInformation()
    reader.UpdateTimeStep(times[-1])
    reader.GetOutputDataObject(0).GetPointData().GetArray("displacement")
    first = time.perf_counter() - t0
    t0 = time.perf_counter()
    for t in times:
        reader.UpdateTimeStep(t)
        out = reader.GetOutputDataObject(0).GetPointData()
        ns.vtk_to_numpy(out.GetArray("displacement")).copy()
        ns.vtk_to_numpy(out.GetArray("stress")).copy()
    full = time.perf_counter() - t0
    return {"size": size_mb(path), "write": write, "first": first, "full": full}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nodes", type=int, nargs="+", default=[100_000])
    parser.add_argument("--steps", type=int, nargs="+", default=[1, 10, 100])
    args = parser.parse_args()
    print("| Nodes | Steps | Store | Size (MB) | Write (s) | First frame (s) | Full read (s) |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for nodes in args.nodes:
        mesh = hex_mesh(nodes)
        for steps in args.steps:
            res = results(mesh, steps)
            work = Path(tempfile.mkdtemp())
            try:
                for name, bench, target in (
                    ("Delta Lake", bench_delta, work / "delta"),
                    ("VTKHDF", bench_vtkhdf, work / "r.vtkhdf"),
                ):
                    r = bench(mesh, res, target)
                    print(
                        f"| {len(mesh.node_ids):,} | {steps} | {name} | {r['size']:.1f} | "
                        f"{r['write']:.2f} | {r['first']:.3f} | {r['full']:.2f} |",
                        flush=True,
                    )
            finally:
                shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
