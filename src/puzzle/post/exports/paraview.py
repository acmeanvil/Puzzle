"""ParaView files (ADR 0043): every field of every step, a .pvd and one .vtu per step."""

from __future__ import annotations


def write(job):
    from puzzle.post.vtk import export as vtu

    return vtu(job.mesh, job.results, job.path), {"steps": str(len(job.results.steps))}
