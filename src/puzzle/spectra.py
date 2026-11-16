"""Response spectra on the finite-element solvers (ADR 0079 step 4, 2026-11-13).

PyNite solves each mode's equivalent static loads itself. CalculiX and Code_Aster give
their modes normalised to unit generalised mass with each mode's participation factor
Γ and effective mass per direction (`participation_factor`, `effective_mass`,
`mass_fraction` global fields, as PyNite's). For a modal step with `response_spectrum`,
`applied` adds a step per direction (kind "spectrum"):

- each mode's response is its shape times q = Γ S_a g I_e / (R ω²), S_a from ASCE 7-16
  Figure 11.4-1 at its period (`frames.seismic.spectrum`), for every nodal field a mode
  carries (displacement, stress);
- the modes combine by CQC (12.9.1.3) at the design's damping, component by component;
- the modal base shear V_t is the CQC of each mode's m_eff S_a g I_e / R, scaled up to
  `scale_to_elf` of the ELF base shear V = C_s W (12.9.1.4.1) at the period of the mode
  with the largest mass in that direction, capped at C_u T_a; stresses are scaled by it,
  the displacements by 12.9.1.4.2's drift scale. W, the levels and h_n are the frame's
  (`frames.seismic_frame.levels`: nodes above the lowest support grouped by elevation,
  each with its members' self weight and the mass cases' loads), as PyNite's, so the
  period cap and the scale agree by solver; a model with no frame is one level of the
  whole mass × g at the height of its nodes above the lowest.

The global field `spectrum_base_shear` holds modal_V, elf_V, scale, period, cs,
drift_scale and mass_participation, as PyNite's does.
"""

from __future__ import annotations

import numpy as np

from puzzle.errors import PuzzleError

KIND = "spectrum"
SOLVERS = frozenset({"calculix", "code_aster"})  # their modes carry participation factors
SCALED = ("stress",)  # forces and stresses: scaled to the ELF base shear
MOVED = ("displacement",)  # motions: by the drift scale only


def wanted(study) -> bool:
    """Whether the study's modal step asks for a response spectrum."""
    return any(s.type == "modal" and s.settings.get("response_spectrum")
               for s in study.solve_steps())  # fmt: skip


def check(study) -> list[str]:
    """What stops a spectrum: the seismic parameters, the directions."""
    if not wanted(study):
        return []
    out = []
    if study.seismic is None:
        out.append("a response spectrum needs the study's seismic parameters (Study.seismic)")
    for s in study.solve_steps():
        if s.type == "modal" and s.settings.get("response_spectrum"):
            bad = [d for d in _directions(s) if d not in ("x", "y")]
            if bad:
                out.append(f"spectrum directions are x and y, not {', '.join(bad)}")
    return out


def _directions(step) -> list[str]:
    text = str(step.settings.get("spectrum_directions", "x,y"))
    return [d.strip() for d in text.split(",") if d.strip()]


def elf_levels(project, study, mesh, total_weight: float) -> tuple[list[dict], float]:
    """The ELF's levels [{height, weight}] and h_n: the frame's when the study's
    component has one (as PyNite's case), else one level of `total_weight` N at the
    height of the mesh's nodes above the lowest."""
    from puzzle.frames import frame_lines
    from puzzle.frames.seismic_frame import levels

    comp = project.objects.get(study.component) if project is not None else None
    if comp is not None and frame_lines(project, comp):
        lv, _base, _notes = levels(project, comp, study.seismic)
        if lv:
            out = [{"height": x["height"], "weight": x["weight"]} for x in lv]
            return out, max(x["height"] for x in out)
    zs = mesh.node_coords(mesh.node_ids)[:, 2]
    height = float(zs.max() - zs.min()) or 1.0
    return [{"height": height, "weight": total_weight}], height


def applied(results, study, mesh, project=None) -> None:
    """Add a spectrum step per direction after the modal steps (see the module)."""
    from puzzle.frames import seismic
    from puzzle.results.model import Field, StepResult

    if not wanted(study):
        return
    problems = check(study)
    if problems:
        raise PuzzleError("; ".join(problems), code="invalid")
    step = next(s for s in study.solve_steps() if s.type == "modal")
    design = study.seismic
    modes = [s for s in results.steps if s.kind == "modal" and s.mode is not None]
    if not modes or any("participation_factor" not in s.fields for s in modes):
        raise PuzzleError(
            "this solver's modes came back without their participation factors, which a "
            "response spectrum needs",
            code="unsupported",
        )
    g = seismic.G
    omegas = np.array([2 * np.pi * s.value for s in modes])
    gamma = np.array([s.fields["participation_factor"].values[0] for s in modes])  # (n, 3)
    meff = np.array([s.fields["effective_mass"].values[0] for s in modes])
    fractions = np.array([s.fields["mass_fraction"].values[0] for s in modes])
    total = float(np.max(meff.sum(axis=0) / np.maximum(fractions.sum(axis=0), 1e-300)))
    sa = np.array([seismic.spectrum(1.0 / s.value, design.sds, design.sd1, design.tl)
                   for s in modes]) * g * design.ie / design.r  # fmt: skip
    elf_lv, height = elf_levels(project, study, mesh, total * g)
    for direction in _directions(step):
        axis = "xyz".index(direction)
        q = gamma[:, axis] * sa / omegas**2  # each mode's modal coordinate
        vt = float(seismic.cqc(meff[:, axis] * sa, omegas, design.damping))
        fundamental = 1.0 / modes[int(np.argmax(fractions[:, axis]))].value
        elf = seismic.elf(design, elf_lv, height, fundamental)
        scale = max(1.0, design.scale_to_elf * elf.base_shear / vt) if vt else 1.0
        drift_scale = 1.0
        if elf.cs_clause == "12.8-6" and vt < elf.cs * elf.weight:
            drift_scale = elf.cs * elf.weight / vt
        fields = {}
        for name in (*MOVED, *SCALED):
            if any(name not in s.fields for s in modes):
                continue
            first = modes[0].fields[name]
            stack = np.array(
                [qi * s.fields[name].at(first.ids) for qi, s in zip(q, modes, strict=True)]
            )
            env = seismic.cqc(stack, omegas, design.damping)
            env = env * (scale if name in SCALED else drift_scale)
            fields[name] = Field(name, first.location, first.components, first.ids, env)
        label = f"E{direction} response spectrum (CQC)"
        fields["combination"] = Field("combination", "global", [label], np.array([0]),
                                      np.array([[0.0]]))  # fmt: skip
        fields["spectrum_base_shear"] = Field(
            "spectrum_base_shear", "global",
            ["modal_V", "elf_V", "scale", "period", "cs", "drift_scale", "mass_participation"],
            np.array([0]),
            np.array([[vt, elf.base_shear, scale, elf.period, elf.cs, drift_scale,
                       float(fractions[:, axis].sum())]]),
        )  # fmt: skip
        results.steps.append(StepResult(modes[0].index, KIND, float(axis), fields=fields))


def participation_fields(gamma, meff, total) -> dict:
    """A mode's global fields from its participation factors and effective masses (x,
    y, z) and the total mass per direction, as PyNite's adapter writes them."""
    from puzzle.results.model import Field

    def one(name, values):
        return Field(name, "global", ["x", "y", "z"], np.array([0]),
                     np.array([list(map(float, values))]))  # fmt: skip

    fraction = [m / t if t else 0.0 for m, t in zip(meff, total, strict=True)]
    return {"participation_factor": one("participation_factor", gamma),
            "effective_mass": one("effective_mass", meff),
            "mass_fraction": one("mass_fraction", fraction)}  # fmt: skip
