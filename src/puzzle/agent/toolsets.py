"""Tool groups for the built-in agent (ADR 0090 package 12, UXA-9).

Every request used to carry all of the MCP server's tools, about 22k tokens before the
user's first word: too much for the small local models ADR 0015 aims at. The built-in
agent now sends a core set (reading the model, changing it, results, help) and loads a
group by area when it needs one: reading a skill loads the groups of the tools the
skill names, `tools_load` loads one by name, and a call to a tool not yet loaded loads
its group first. Loading changes only what is listed to the model: whether a tool may
act is still the session's (autonomy levels, `session.ACTIONS`, the budget), so no tool
is reachable below its level by being loaded.

Outside MCP clients (`puzzle mcp`) are sent every tool, as before: they pay for their
own context and have their own ways of choosing tools.

`ANNOTATIONS` gives each tool MCP's read-only and destructive hints (UXA-12), so a
client can tell reads from writes for its own permission prompts.
"""

from __future__ import annotations

import re
from functools import cache

CORE = (
    "outline", "get_object", "schema", "problems", "check_study", "next_steps",
    "submit", "propose", "preview", "undo", "redo", "history", "proposals",
    "skill_read", "tools_load", "plan", "knowledge", "help", "analysis_new",
    "materials_library", "capabilities", "autonomy", "runs", "run_progress",
    "result_summary", "result_stats", "result_value_at", "result_total", "render",
)  # fmt: skip

GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "geometry": ("checking and measuring geometry, selections, sketches, splitting parts",
                 ("geometry_check", "selections_check", "sketch_check", "geometry_measure",
                  "parts_split")),
    "contacts": ("finding and checking contacts, threads, weak springs",
                 ("contacts_find", "contact_check", "contact_results", "thread_forms",
                  "contact_thread", "thread_results", "weak_springs")),
    "bolts": ("bolts: generating, sizes, fastener tables, VDI 2230, modelled or beam, "
              "results",
              ("bolts_generate", "bolt_make_modelled", "bolt_make_beam", "bolt_results",
               "bolt_sizes", "fastener_tables", "bolt_vdi2230")),
    "welds": ("welds: adding, modelled or line, results, fatigue",
              ("weld_add", "weld_make_modelled", "weld_make_line", "weld_results",
               "weld_fatigue")),
    "frames": ("frames of members, section tables, member design, seismic, combinations",
               ("frame_geometry", "frame_grid", "frame_members", "member_design",
                "member_checks", "seismic_design", "seismic_elf", "seismic_report",
                "load_combinations", "sections_find")),
    "mesh": ("mesh quality, element types, convergence, error estimates, adaptivity, "
             "mesh shoot-outs",
             ("mesh_quality", "mesh_shootout", "mesh_adapt", "error_estimate",
              "mesh_convergence", "elements_list", "element_choose")),
    "physics": ("symmetry, magnetics checks, explicit dynamics estimates",
                ("symmetry_plane", "symmetry_repeat", "magnetic_check", "explicit_estimate")),
    "materials": ("checking materials, exporting the library",
                  ("material_check", "materials_export")),
    "fields": ("imported fields: importing and checking them",
               ("field_import", "field_check")),
    "results": ("more results: derived results, probes and paths, integrals, histories, "
                "exports, solver logs",
                ("derived_result", "equation_names", "probes", "probe_history", "probe_path",
                 "result_integral", "result_history", "export", "run_log")),
    "operators": ("operators, recipes and the schematic: values, adding, wiring, exports",
                  ("recipes", "recipe_insert", "operator_value", "operator_export",
                   "operator_add", "link", "unlink", "operator_help", "model_values",
                   "schematic")),
    "optimisation": ("design of experiments and optimisers",
                     ("optimisation_points", "optimisation_run", "optimisation_apply")),
    "solvers": ("solvers here, their settings, solver shoot-outs, remote hosts, the job runner",
                ("solvers_list", "pardiso_build", "solver_settings", "solver_settings_set",
                 "solver_shootout",
                 "hosts_list", "host_check", "jobs_list", "job_resume", "job_cancel")),
    "project": ("versions, archives, the skills list",
                ("versions_list", "version_save", "version_restore", "project_archive",
                 "skills_list")),
}  # fmt: skip

# What reads only (MCP's readOnlyHint) and what can lose work (destructiveHint). The
# rest change the project through the session, undoably, or write files.
READ_ONLY = frozenset({
    "skills_list", "skill_read", "outline", "get_object", "schema", "geometry_check",
    "selections_check", "contacts_find", "contact_check", "field_check", "frame_members",
    "member_checks", "seismic_report", "mesh_quality", "explicit_estimate", "sketch_check",
    "material_check", "problems", "materials_library", "capabilities", "check_study",
    "recipes", "preview", "history", "proposals", "knowledge", "solvers_list",
    "hosts_list", "jobs_list", "versions_list", "autonomy", "derived_result",
    "equation_names", "geometry_measure", "probes", "probe_history", "probe_path",
    "solver_settings", "elements_list", "help", "next_steps", "optimisation_points",
    "error_estimate", "magnetic_check", "operator_value", "operator_help", "model_values",
    "schematic", "runs", "bolt_results", "contact_results", "thread_forms",
    "thread_results", "weld_results", "weld_fatigue", "run_log", "run_progress",
    "sections_find", "bolt_sizes", "fastener_tables", "bolt_vdi2230", "result_summary",
    "result_stats", "result_value_at", "result_integral", "result_total",
    "result_history", "render", "tools_load", "plan",
    # planners: they return the commands to submit and change nothing themselves
    "frame_geometry", "frame_grid", "parts_split", "bolts_generate", "bolt_make_modelled",
    "bolt_make_beam", "weld_add", "weld_make_modelled", "weld_make_line", "field_import",
    "weak_springs",
})  # fmt: skip
DESTRUCTIVE = frozenset({"version_restore", "job_cancel", "unlink"})

# Tools whose output holds text from outside Puzzle (a solver's log echoing its input,
# a file's headers, an attachment): wrapped as untrusted for the model (UXA-11).
UNTRUSTED = frozenset({"run_log", "field_import", "field_check"})


def group_of(tool: str) -> str | None:
    """The group a tool is in ("core" for the core set), None for an unknown tool."""
    if tool in CORE:
        return "core"
    for name, (_what, tools) in GROUPS.items():
        if tool in tools:
            return name
    return None


def tools_of(group: str) -> tuple[str, ...]:
    if group == "core":
        return CORE
    if group not in GROUPS:
        raise KeyError(f"no tool group '{group}'; the groups are: {', '.join(GROUPS)}")
    return GROUPS[group][1]


@cache
def skill_groups() -> dict[str, tuple[str, ...]]:
    """Each skill's groups: those of the tools it names in backticks."""
    from puzzle.agent.skills import skills

    out = {}
    for name, skill in skills().items():
        named = set(re.findall(r"`([a-z_]+)", skill.body))
        found = {group_of(t) for t in named} - {None, "core"}
        out[name] = tuple(sorted(found))
    return out


def catalogue() -> str:
    """The groups in one line each, for the prompt and `tools_load`'s description."""
    return "\n".join(f"- {name}: {what}" for name, (what, _t) in GROUPS.items())
