"""Model → Studies → Seismic design… and ELF loads… (ADR 0079 step 4): one form setting a
frame study's ASCE 7-16 parameters (one Edit), and one writing the ELF story forces as
loads (one Edit), saying the base shear and how C_s was found. The same as MCP
`seismic_design` and `seismic_elf`."""

from __future__ import annotations

from puzzle.commands.model import Edit, EditOp
from puzzle.gui.quick_form import Ask

STRUCTURES = [
    ("All other structural systems", "other"),
    ("Steel moment-resisting frames", "steel_moment_frame"),
    ("Concrete moment-resisting frames", "concrete_moment_frame"),
    ("Steel eccentrically braced frames", "steel_eccentrically_braced"),
    ("Steel buckling-restrained braced frames", "steel_buckling_restrained"),
]
DRIFTS = [
    ("All other structures", "other"),
    ("Four stories or less, partitions designed for drift", "low_rise_with_partitions"),
    ("Masonry cantilever shear walls", "masonry_cantilever_shear_wall"),
    ("Other masonry shear walls", "other_masonry_shear_wall"),
]
RISK = [("I or II", "II"), ("III", "III"), ("IV", "IV")]
NUMBERS = [  # key, label, default, help
    ("sds", "S_DS (g)", "", "design short-period spectral acceleration (11.4.4)"),
    ("sd1", "S_D1 (g)", "", "design 1 s spectral acceleration"),
    ("s1", "S_1 (g)", "0", "mapped MCE_R 1 s acceleration, for 12.8-6"),
    ("tl", "T_L (s)", "8", "long-period transition (Figures 22-14 to 22-17)"),
    ("r", "R", "", "response modification coefficient (Table 12.2-1)"),
    ("cd", "C_d", "", "deflection amplification factor (Table 12.2-1)"),
    ("ie", "I_e", "1", "importance factor (Table 1.5-2)"),
]


def _check(values: dict) -> str | None:
    for key, label, _d, _h in NUMBERS:
        try:
            v = float(values[key])
        except (TypeError, ValueError):
            return f"{label}: a number"
        if v < 0 or (v == 0 and key not in ("s1",)):
            return f"{label} must be positive"
    return None


def edit_seismic(window, study):
    from puzzle.model.schema import SeismicDesign

    now = study.seismic
    asks = [Ask(key, label, kind="text",
                default=f"{getattr(now, key):g}" if now else default, help=help_)
            for key, label, default, help_ in NUMBERS]  # fmt: skip
    asks += [
        Ask("risk_category", "Risk category", choices=RISK,
            default=now.risk_category if now else "II"),
        Ask("structure", "Structure (for T_a)", choices=STRUCTURES,
            default=now.structure if now else "other"),
        Ask("drift_structure", "Drift limit (Table 12.12-1)", choices=DRIFTS,
            default=now.drift_structure if now else "other"),
    ]  # fmt: skip
    got = window.ask_form(
        f"Seismic design of {study.name}", asks,
        intro="ASCE 7-16 parameters. W is the members' weight plus the D loads; ELF loads… "
        "writes the story forces, or a modal step with a response spectrum runs 12.9.",
        check=_check)  # fmt: skip
    if got is None:
        return None
    values = {k: float(got[k]) for k, *_ in NUMBERS}
    values |= {k: got[k] for k in ("risk_category", "structure", "drift_structure")}
    if now is not None:
        values = {**now.model_dump(), **values}
    design = SeismicDesign(**values)
    return window.submit(Edit(object=str(study.id), ops=[EditOp(
        op="set", path="seismic", value=design.model_dump(mode="json"))]))  # fmt: skip


def write_elf(window, study):
    from puzzle.frames.seismic_frame import elf_commands
    from puzzle.model.schema import Component

    if study.seismic is None:
        window.toasts.show("Give the study its seismic design first (Seismic design…).",
                           state="info")  # fmt: skip
        return None
    intro = (
        "ASCE 7-16 12.8: the base shear spread over the levels, written as loads of "
        "case Ex or Ey (earlier ELF loads that way replaced)."
    )
    direction = Ask("direction", "Direction", choices=[("x", "x"), ("y", "y")])
    got = window.ask_form(f"ELF loads for {study.name}", [direction], intro=intro)
    if got is None:
        return None
    project = window.session.project
    comp = project.get(study.component, Component)
    edit, result, notes = elf_commands(project, comp, study, got["direction"])
    done = window.submit(edit)
    window.toasts.show(
        f"ELF along {got['direction']}: V = {result.base_shear / 1e3:.4g} kN, C_s "
        f"{result.cs:.4g} ({result.cs_clause}), T {result.period:.3g} s"
        + (f". {' '.join(notes)}" if notes else ""), state="info")  # fmt: skip
    return done
