"""VTKHDF (ADR 0043): one HDF5 file holding the mesh once and every step's fields, which
ParaView (5.12 and later) and VTK's `vtkHDFReader` open as a time series. Written by VTK's
`vtkHDFWriter` from a small source that hands it one step per time value.

Point and cell data are the stored fields (SI: m, Pa, K, ...), with `node_id` and
`element_id` carrying Puzzle's numbers; a field missing from a step is NaN there, as
the format keeps one array list for the whole series. Global values (a stored energy,
bolt forces) go in each step's field data, under the same names.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from puzzle.post.exports.series import fields, step_times, values_at


def write(job):
    return write_vtkhdf(job.mesh, job.results, job.path)


def write_vtkhdf(mesh, results, path: Path):
    """Every step of `results` on `mesh` as one .vtkhdf file; returns ([path], notes)."""
    from vtkmodules.vtkIOHDF import vtkHDFWriter

    from puzzle.post.vtk import to_grid

    steps = results.steps
    times, what = step_times(steps)
    kinds = fields(steps)
    source = _series_class()(to_grid(mesh), steps, times, kinds)
    writer = vtkHDFWriter()
    writer.SetInputConnection(source.GetOutputPort())
    writer.SetFileName(str(path))
    writer.SetWriteAllTimeSteps(len(steps) > 1)
    if not writer.Write():
        from puzzle.errors import PuzzleError

        raise PuzzleError(f"VTK could not write {path.name}", code="export_failed")
    notes = {"time values": what, "units": "SI (m, Pa, K, ...)", "steps": str(len(steps))}
    if what.startswith("step number"):
        notes["steps"] = "; ".join(f"{i + 1}: {s.label()}" for i, s in enumerate(steps))
    return [path], notes


def _series_class():
    """The source class, built on first use so VTK loads only when exporting."""
    from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase
    from vtkmodules.vtkCommonDataModel import vtkDataObject
    from vtkmodules.vtkCommonExecutionModel import vtkStreamingDemandDrivenPipeline as SDDP

    class Series(VTKPythonAlgorithmBase):
        """Hands the writer the grid with one step's fields per requested time."""

        def __init__(self, grid, steps, times, kinds):
            super().__init__(nInputPorts=0, nOutputPorts=1, outputType="vtkUnstructuredGrid")
            self.grid, self.steps, self.times, self.kinds = grid, steps, times, kinds

        def RequestInformation(self, request, inInfo, outInfo):
            info = outInfo.GetInformationObject(0)
            info.Set(SDDP.TIME_STEPS(), self.times, len(self.times))
            info.Set(SDDP.TIME_RANGE(), [self.times[0], self.times[-1]], 2)
            return 1

        def RequestData(self, request, inInfo, outInfo):
            info = outInfo.GetInformationObject(0)
            t = info.Get(SDDP.UPDATE_TIME_STEP()) if info.Has(SDDP.UPDATE_TIME_STEP()) else None
            k = 0 if t is None else int(np.argmin(np.abs(np.asarray(self.times) - t)))
            grid = _step_grid(self.grid, self.steps[k], self.kinds)
            out = vtkDataObject.GetData(outInfo)
            out.ShallowCopy(grid)
            out.GetInformation().Set(vtkDataObject.DATA_TIME_STEP(), self.times[k])
            return 1

    return Series


def _step_grid(base, step, kinds):
    """A shallow copy of the mesh grid with the step's nodal, element and global fields."""
    import pyvista as pv

    grid = base.copy(deep=False)
    node_ids = np.asarray(base.point_data["node_id"])
    element_ids = np.asarray(base.cell_data["element_id"])
    for k in kinds:
        field = step.fields.get(k.name)
        if field is not None and field.location != k.location:
            field = None
        width = len(k.components)
        if k.location == "node":
            values = values_at(field, node_ids, width)
            grid.point_data[k.name] = values[:, 0] if width == 1 else values
        elif k.location == "element":
            values = values_at(field, element_ids, width)
            grid.cell_data[k.name] = values[:, 0] if width == 1 else values
        else:
            values = np.full(width, np.nan) if field is None else np.asarray(field.values, float)
            array = pv.convert_array(np.atleast_2d(values).astype(float), name=k.name)
            grid.GetFieldData().AddArray(array)
    return grid
