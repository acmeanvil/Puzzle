"""The session: the one place a project changes (ADR 0015).

Every client (GUI, CLI, scripts, the built-in agent, outside agents over MCP)
submits commands here. A session:

- checks the actor against the project's autonomy level (agents only);
- applies the command, saves the project and appends an entry to history.jsonl,
  with the objects' prior state so it can be undone;
- turns an agent's command into a proposal when the level is "propose", for a
  user to accept or reject.

history.jsonl is append-only. Entry types: "command" (applied), "undo",
"proposal", "decision" (a proposal accepted or rejected) and
"confirmation_request" (the first half of a double sign-off).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from puzzle.commands.model import (
    AddLibraryMaterial,
    CopyComponent,
    CopyStudy,
    Create,
    Delete,
    Edit,
    EditOp,
    RunStudy,
    SetAutonomy,
    _Command,
    parse_command,
)
from puzzle.errors import Problem, PuzzleError
from puzzle.model.schema import OBJECT_TYPES, Component, Project, ProjectObject, Study
from puzzle.project.layout import HISTORY, LOCK, PROJECT_FILE, RESULTS_DIR, WORK_DIR
from puzzle.project.store import load_project, save_project

KINDS: dict[str, type[ProjectObject]] = {cls.kind: cls for cls in OBJECT_TYPES}
CONFIRM_WITHIN = timedelta(minutes=15)
STALE_LOCK_S = 120.0  # the lock is never held while a solver runs


@dataclass(frozen=True)
class Actor:
    kind: Literal["user", "agent", "script"]
    name: str = ""
    # ADR 0022: which model an agent ran on, and the evaluation suite it was picked by,
    # so a project's history shows which model made which change.
    model: str = ""
    suite: str = ""

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"

    def to_dict(self) -> dict[str, str]:
        out = {"kind": self.kind, "name": self.name}
        out |= {k: v for k, v in (("model", self.model), ("suite", self.suite)) if v}
        return out


USER = Actor("user")


def _same_actor(logged: dict, actor: Actor) -> bool:
    """Whether a history entry's actor is this one: kind and name only, since the model
    and suite (ADR 0022) are provenance; an agent moved to another model still owns
    its edits."""
    return (logged or {}).get("kind") == actor.kind and (logged or {}).get("name") == actor.name


@dataclass
class Outcome:
    """What a submitted command did."""

    status: Literal["applied", "proposed"]
    seq: int | None = None  # history entry, when applied
    proposal: str | None = None  # proposal id, when proposed
    created: list[dict[str, str]] = field(default_factory=list)
    changed: list[dict[str, str]] = field(default_factory=list)
    deleted: list[dict[str, str]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)  # the project's, afterwards
    run: Any = None  # the StudyRun, for in-process clients of run_study; not serialized

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        for key in ("seq", "proposal"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        for key in ("created", "changed", "deleted"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.data:
            out["data"] = self.data
        out["problems"] = [p.to_dict() for p in self.problems]
        return out


class Session:
    """A project directory opened for changes."""

    def __init__(self, root: Path) -> None:
        if not (root / PROJECT_FILE).exists():
            raise PuzzleError(f"{root} is not a Puzzle project", code="not_found")
        self.root = root
        self.project = load_project(root)
        self._opened_checked = False  # ADR 0059: one "opened" version check per session

    # --- submitting --------------------------------------------------------------

    def submit(
        self,
        command: dict[str, Any] | _Command,
        actor: Actor = USER,
        *,
        rationale: str = "",
        on_output=None,
        on_event=None,
        cancel=None,
    ) -> Outcome:
        """Apply `command` for `actor`, or record it as a proposal (agents at "propose").
        A run reports progress events to `on_event` (ADR 0054) and stops when `cancel`
        (a threading.Event) is set."""
        cmd = parse_command(command)
        with self._locked():
            decision = self._authorize(cmd, actor)
            if decision == "propose":
                return self._propose([cmd], actor, rationale)
            if not isinstance(cmd, RunStudy):
                return self._apply(cmd, actor)
        if cmd.resume:
            return self._resume(cmd, on_output, on_event, cancel, actor)
        return self._run_command(cmd, actor, on_output, on_event, cancel)

    def _run_command(self, cmd: RunStudy, actor: Actor, on_output, on_event, cancel, *,
                     workdir: Path | None = None, attach: dict[str, str] | None = None,
                     resumed: str | None = None) -> Outcome:  # fmt: skip
        with self._locked():
            study = resolve(self.project, cmd.study, Study)
            timeout = self._run_timeout(cmd, actor)
            snapshot = copy.deepcopy(self.project)
            solved_from = self._auto_version(
                "before_run", actor, words={"study": study.name}, always_latest=True
            )
        # Solve outside the lock, so other clients can keep working meanwhile.
        run, data = self._run(snapshot, study, cmd, timeout, on_output, on_event, cancel,
                              workdir=workdir, attach=attach)  # fmt: skip
        if resumed:
            data["resumed"] = resumed
        with self._locked():
            seq = self._log(
                {"type": "command", "actor": actor.to_dict(), "command": _dump(cmd), "result": data}
            )
            if solved_from is not None:  # the state it solved from works (ADR 0059)
                self._mark_solved(solved_from, study.name)
        return Outcome("applied", seq=seq, data=data, problems=self.project.problems(), run=run)

    def pending_jobs(self) -> list[dict]:
        """Solves handed to a job runner whose results are not in yet (ADR 0067): those
        still running, and those that ended while no Puzzle was following them."""
        from puzzle.jobs import pending

        return pending.entries(self.root)

    def resume(self, job: str, actor: Actor = USER, *, on_output=None, on_event=None,
               cancel=None) -> Outcome:  # fmt: skip
        """Pick up a pending job: the same run (study, run folder) attached to the job
        instead of submitting it again; finished points and upstream studies are reused.
        A run like any other (a `run_study` command with `resume`): agents need the
        autonomy to run, and at "propose" accepting the proposal picks the job up."""
        entry = self._pending_entry(job)
        cmd = RunStudy(study=entry["study"], reuse=True, host=entry.get("host"), resume=job)
        return self.submit(cmd, actor, rationale=f"pick up job {job}", on_output=on_output,
                           on_event=on_event, cancel=cancel)  # fmt: skip

    def _pending_entry(self, job: str) -> dict:
        from puzzle.jobs import pending

        mine = [e for e in pending.entries(self.root) if e["job"] == job]
        if not mine:
            raise PuzzleError(f"no pending job {job}", code="not_found")
        entry = mine[0]
        try:
            resolve(self.project, entry.get("study") or "", Study)
        except PuzzleError:
            pending.remove(self.root, job)
            raise PuzzleError(f"job {job}'s study is gone from the project",
                              code="not_found") from None  # fmt: skip
        if pending.followed(entry):
            raise PuzzleError(f"job {job} is already being followed by another Puzzle "
                              f"(process {entry['follower']['pid']})", code="busy")  # fmt: skip
        return entry

    def _resume(self, cmd: RunStudy, on_output, on_event, cancel, actor: Actor) -> Outcome:
        from puzzle.jobs import pending

        entry = self._pending_entry(cmd.resume)
        same = {e["folder"]: e["job"] for e in pending.entries(self.root)
                if e.get("workdir") == entry["workdir"]}  # fmt: skip
        return self._run_command(
            cmd, actor, on_output, on_event, cancel,
            workdir=Path(entry["workdir"]), attach=same, resumed=cmd.resume,
        )  # fmt: skip

    def submit_batch(
        self, commands: list[dict[str, Any] | _Command], actor: Actor = USER, *, rationale: str = ""
    ) -> Outcome:
        """Apply model edits together: all or none, one history entry, one undo.

        Each command runs on a copy of the project first, so a failure part-way leaves
        the project as it was. Runs and autonomy changes are not model edits; submit
        them on their own. At "propose" the batch becomes one proposal.
        """
        cmds = [parse_command(c) for c in commands]
        if not cmds:
            raise PuzzleError("an empty batch", code="invalid")
        odd = [c.command for c in cmds if not c.edits_model]
        if odd:
            raise PuzzleError(
                f"{', '.join(sorted(set(odd)))} cannot go in a batch; submit it on its own",
                code="invalid",
            )
        with self._locked():
            decisions = {self._authorize(c, actor) for c in cmds}
            if "propose" in decisions:
                return self._propose(cmds, actor, rationale)
            self._check_opened(actor)
            before = _snapshot(self.project)
            trial = copy.deepcopy(self.project)
            results = []
            for i, cmd in enumerate(cmds):
                try:
                    results.append(_execute(trial, cmd))
                except PuzzleError as exc:
                    exc.details = {**exc.details, "failed_command": i, "applied": "none"}
                    raise
            self.project = trial
            dumped = {"command": "batch", "commands": [_dump(c) for c in cmds]}
            extra = {"rationale": rationale} if rationale else {}
            out = self._record(dumped, actor, before, extra=extra, data={"steps": results})
            self._edits_version(actor)
            return out

    def propose(
        self, commands: list[dict[str, Any] | _Command], actor: Actor, *, rationale: str = ""
    ) -> Outcome:
        """Record commands as one proposal, whatever the autonomy level."""
        cmds = [parse_command(c) for c in commands]
        with self._locked():
            if actor.is_agent and self.project.agent.level == "advise":
                raise PuzzleError("agents only advise in this project", code="not_allowed")
            for cmd in cmds:
                if isinstance(cmd, SetAutonomy):
                    raise PuzzleError("autonomy is set by users only", code="not_allowed")
            return self._propose(cmds, actor, rationale)

    def preview(self, commands: list[dict[str, Any] | _Command]) -> dict[str, Any]:
        """Apply commands to a copy of the project: what would change, and its problems."""
        trial = copy.deepcopy(self.project)
        before = _snapshot(trial)
        steps = []
        for cmd in (parse_command(c) for c in commands):
            if isinstance(cmd, RunStudy):
                study = resolve(trial, cmd.study, Study)
                steps.append({"command": "run_study", "would_run": study.name})
            elif isinstance(cmd, SetAutonomy):
                steps.append({"command": "set_autonomy", "would_set": _dump(cmd)})
            else:
                steps.append({"command": cmd.command, **_execute(trial, cmd)})
        created, changed, deleted = _diff(before, _snapshot(trial))
        return {
            "steps": steps,
            "created": _describe(trial, created),
            "changed": _describe(trial, changed),
            "deleted": [_brief(before[i]) for i in deleted],
            "problems": [p.to_dict() for p in trial.problems()],
        }

    # --- proposals ------------------------------------------------------------------

    def proposals(self, *, open_only: bool = True) -> list[dict[str, Any]]:
        entries = self.history_entries()
        decided = {e["proposal"]: e for e in entries if e["type"] == "decision"}
        out = []
        for e in entries:
            if e["type"] != "proposal":
                continue
            decision = decided.get(e["id"])
            if open_only and decision:
                continue
            out.append(e | ({"decision": decision} if decision else {}))
        return out

    def accept(
        self, proposal_id: str, actor: Actor = USER, *, only: list[int] | None = None
    ) -> list[Outcome]:
        """Apply a proposal (or the listed commands of it), attributed to its author."""
        if actor.is_agent:
            raise PuzzleError("only a user can accept a proposal", code="not_allowed")
        proposal = self._open_proposal(proposal_id)
        author = Actor(**proposal["actor"])
        picks = range(len(proposal["commands"])) if only is None else only
        outcomes = []
        for i in picks:
            cmd = parse_command(proposal["commands"][i])
            if isinstance(cmd, RunStudy):
                outcomes.append(self.submit(cmd, actor))
                continue
            with self._locked():
                outcomes.append(self._apply(cmd, author, approved_by=actor, proposal=proposal_id))
        with self._locked():
            self._log(
                {
                    "type": "decision",
                    "proposal": proposal_id,
                    "actor": actor.to_dict(),
                    "accepted": list(picks),
                }
            )
        return outcomes

    def reject(self, proposal_id: str, actor: Actor = USER, *, reason: str = "") -> None:
        if actor.is_agent:
            raise PuzzleError("only a user can reject a proposal", code="not_allowed")
        self._open_proposal(proposal_id)
        with self._locked():
            self._log(
                {
                    "type": "decision",
                    "proposal": proposal_id,
                    "actor": actor.to_dict(),
                    "accepted": [],
                    "reason": reason,
                }
            )

    # --- undo ---------------------------------------------------------------------

    def undo(self, actor: Actor = USER, *, seq: int | None = None) -> Outcome:
        """Undo one applied entry (default: the latest). Undoing an undo redoes it.

        Agents may undo only their own changes, and only above "advise".
        """
        with self._locked():
            if actor.is_agent and self.project.agent.level == "advise":
                raise PuzzleError("agents only advise in this project", code="not_allowed")
            entries = {e["seq"]: e for e in self.history_entries() if "seq" in e}
            live = _in_effect(entries)
            if seq is None:
                candidates = [
                    s
                    for s, e in entries.items()
                    if s in live
                    and e.get("undo")
                    and (not actor.is_agent or _same_actor(e["actor"], actor))
                ]
                if not candidates:
                    raise PuzzleError("nothing to undo", code="not_undoable")
                seq = max(candidates)
            entry = entries.get(seq)
            if entry is None:
                raise PuzzleError(f"no history entry {seq}", code="not_found")
            if not entry.get("undo") or seq not in live:
                raise PuzzleError(f"entry {seq} cannot be undone", code="not_undoable")
            if actor.is_agent and not _same_actor(entry["actor"], actor):
                raise PuzzleError("agents may undo only their own changes", code="not_allowed")
            state = entry["undo"]
            current = _snapshot(self.project)
            stale = [
                i for i, h in state["after"].items() if (current.get(i) or {}).get("hash") != h
            ]
            if stale:
                names = [current[i]["name"] if i in current else i for i in stale]
                raise PuzzleError(
                    "changed since; undo the later changes first",
                    code="not_undoable",
                    details={"objects": names},
                )
            before = current
            if (entry.get("command") or {}).get("command") == "restore_version" and entry.get(
                "files_from"
            ):  # a restore's files go back too (ADR 0059)
                from puzzle.project import versions

                versions.restore_files(self.root, versions.get(self.root, entry["files_from"]))
                self.project = load_project(self.root)
            for i in state["after"]:
                self.project.objects.pop(UUID(i), None)
            for snap in state["before"].values():
                obj = KINDS[snap["kind"]].model_validate(snap["data"])
                self.project.objects[obj.id] = obj
            return self._record(
                {"command": "undo", "seq": seq}, actor, before, extra={"undoes": seq}
            )

    # --- versions (ADR 0059) ------------------------------------------------------

    def save_version(
        self, actor: Actor = USER, *, label: str = "", note: str = "", good: bool = False
    ):
        """A version of the project as it is now, saved on purpose (always made)."""
        from puzzle.project import versions

        with self._locked():
            return versions.save(
                self.root, how="user", trigger="user", actor=actor.to_dict(), label=label,
                note=note, good=good, good_reason="marked by you" if good else "",
                history_seq=self._last_seq(), project=self.project,
            )  # fmt: skip

    def restore_version(self, number: int | str, actor: Actor = USER) -> Outcome:
        """Make the project version `number`'s (or "good": the latest known good). The
        state replaced is kept as a "before restoring" version first, and the restore
        is one history entry, so undo takes it back."""
        from puzzle.project import versions

        if actor.is_agent and self.project.agent.level not in ("act", "act_in_budget"):
            raise PuzzleError(
                "restoring a version replaces much at once: ask the user to restore it",
                code="not_allowed",
            )
        with self._locked():
            target = versions.get(self.root, number)
            kept = self._auto_version(
                "before_restore", actor, words={"version": target.number},
                always_latest=True, forced=True,
            )  # fmt: skip
            before = _snapshot(self.project)
            changed = versions.restore_files(self.root, target)
            self.project = load_project(self.root)
            return self._record(
                {"command": "restore_version", "version": target.number}, actor, before,
                extra={"restored": target.number, "files_from": kept.number if kept else None,
                       "files": changed},
            )  # fmt: skip

    def mark_version(self, number: int, good: bool = True, actor: Actor = USER):
        from puzzle.project import versions

        if actor.is_agent and self.project.agent.level == "advise":
            raise PuzzleError("agents only advise in this project", code="not_allowed")
        with self._locked():
            return versions.mark(self.root, number, good, "marked by you" if good else "")

    def _last_seq(self) -> int:
        return max((e.get("seq", 0) for e in self.history_entries()), default=0)

    def _versions_on(self) -> bool:
        from puzzle.settings import current

        try:
            return bool(current().get("versions.auto"))
        except Exception:  # noqa: BLE001 - no settings (a bare script): on
            return True

    def _auto_version(
        self, trigger: str, actor: Actor, *, words=None, always_latest=False, forced=False
    ):
        """An automatic version when something changed since the latest; with
        `always_latest`, the latest version when nothing did (the state it holds).
        `forced` makes one even with automatic versions off (before a restore)."""
        from puzzle.project import versions

        if not forced and not self._versions_on():
            return None
        try:
            made = versions.save(
                self.root, how="auto", trigger=trigger, actor=actor.to_dict(),
                history_seq=self._last_seq(), project=self.project, words=words,
                only_if_changed=True,
            )  # fmt: skip
        except OSError:  # a read-only or full disk never stops the work itself
            return None
        return made if made is not None or not always_latest else versions.latest(self.root)

    def _check_opened(self, actor: Actor) -> None:
        """Before this session's first change: a version of the project as found, when
        it changed outside Puzzle (or has none yet)."""
        if self._opened_checked:
            return
        self._opened_checked = True
        from puzzle.project import versions

        first = versions.latest(self.root) is None
        self._auto_version("first" if first else "opened", actor)

    def _edits_version(self, actor: Actor) -> None:
        from puzzle.project import versions
        from puzzle.settings import current

        if not self._versions_on():
            return
        try:
            every = int(current().get("versions.every_edits"))
            minutes = float(current().get("versions.every_minutes"))
        except Exception:  # noqa: BLE001
            every, minutes = 20, 15.0
        now, count = versions.due(self.root, every, minutes)
        if now:
            self._auto_version("edits", actor, words={"count": count})

    def _mark_solved(self, v, study: str) -> None:
        from puzzle.project import versions

        try:
            current = versions.get(self.root, v.number)
            reason = "; ".join(r for r in (current.good_reason, f"solved {study}") if r)
            versions.mark(self.root, v.number, True, reason)
        except (PuzzleError, OSError):
            pass

    # --- reading ---------------------------------------------------------------

    def history_entries(self) -> list[dict[str, Any]]:
        path = self.root / HISTORY
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        """The latest entries, newest first, without the undo snapshots."""
        entries = self.history_entries()
        live = _in_effect({e["seq"]: e for e in entries if "seq" in e})
        out = []
        for e in reversed(entries[-limit:]):
            brief = {k: v for k, v in e.items() if k != "undo"}
            if e.get("undo") is not None:
                brief["undone"] = e["seq"] not in live
            out.append(brief)
        return out

    def budget_used(self) -> dict[str, float]:
        """Fresh agent runs and solver seconds since autonomy was last set."""
        runs, seconds, core_hours = 0, 0.0, 0.0
        for e in self.history_entries():
            command = e.get("command") or {}
            if e["type"] == "command" and command.get("command") == "set_autonomy":
                runs, seconds, core_hours = 0, 0.0, 0.0
            elif (
                e["type"] == "command"
                and command.get("command") == "run_study"
                and e["actor"]["kind"] == "agent"
                and not e.get("result", {}).get("cached")
            ):
                runs += 1
                seconds += e.get("result", {}).get("duration_s", 0.0)
                core_hours += e.get("result", {}).get("core_hours", 0.0)
        return {"runs": runs, "solver_seconds": seconds, "core_hours": core_hours}

    # --- internals --------------------------------------------------------------

    def _authorize(self, cmd: _Command, actor: Actor) -> Literal["apply", "propose"]:
        if not actor.is_agent:
            return "apply"
        level = self.project.agent.level
        if isinstance(cmd, SetAutonomy):
            raise PuzzleError("autonomy is set by users only", code="not_allowed")
        if level == "advise":
            raise PuzzleError(
                "agents only advise in this project; describe the change instead",
                code="not_allowed",
            )
        if level == "propose":
            return "propose"
        if level != "act" and self._writes_code(cmd):  # ADR 0062: new code is proposed
            return "propose"
        return "apply"

    def _writes_code(self, cmd: _Command) -> bool:
        """Whether a command writes code that would run (a script operator, a custom
        PDE's weak form): below "act" an agent proposes it, the user accepts."""
        if isinstance(cmd, Create):
            data = cmd.data
            return (cmd.kind == "operator" and data.get("type") == "script") or (
                cmd.kind == "component" and "equation" in json.dumps(data)
            )
        if isinstance(cmd, Edit):
            try:
                obj = resolve(self.project, cmd.object)
            except PuzzleError:
                return False
            if getattr(obj, "type", None) == "script":
                return True
            return any(
                "equation" in op.path
                or op.path.endswith("code")
                or (op.path == "type" and op.value == "script")
                for op in cmd.ops
            )
        return False

    def _run_timeout(self, cmd: RunStudy, actor: Actor) -> float | None:
        if not actor.is_agent or self.project.agent.level != "act_in_budget":
            return cmd.timeout_s
        budget, used = self.project.agent.budget, self.budget_used()
        left = budget.solver_seconds - used["solver_seconds"]
        remote = cmd.host or getattr(getattr(resolve(self.project, cmd.study, Study), "remote",
                                             None), "host", None)  # fmt: skip
        hours_left = budget.core_hours - used["core_hours"]
        if remote and hours_left <= 0:  # ADR 0021: remote time can cost money
            raise PuzzleError(
                "the agent's remote core-hours are spent; propose the run instead",
                code="budget_exceeded",
                details={"budget": budget.model_dump(), "used": used},
            )
        if used["runs"] >= budget.runs or left <= 0:
            raise PuzzleError(
                "the agent's run budget is spent; propose the run instead",
                code="budget_exceeded",
                details={"budget": budget.model_dump(), "used": used},
            )
        return min(cmd.timeout_s or left, left)

    def _apply(
        self,
        cmd: _Command,
        actor: Actor,
        *,
        approved_by: Actor | None = None,
        proposal: str | None = None,
    ) -> Outcome:
        if isinstance(cmd, SetAutonomy):
            return self._set_autonomy(cmd, actor)
        self._check_opened(actor)
        before = _snapshot(self.project)
        data = _execute(self.project, cmd)
        extra: dict[str, Any] = {"result": data} if data else {}
        if approved_by:
            extra |= {"approved_by": approved_by.to_dict(), "proposal": proposal}
        out = self._record(_dump(cmd), actor, before, extra=extra, data=data)
        self._edits_version(actor)
        return out

    def _record(
        self,
        command: dict[str, Any],
        actor: Actor,
        before: dict[str, dict],
        *,
        extra: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Outcome:
        after = _snapshot(self.project)
        created, changed, deleted = _diff(before, after)
        save_project(self.project, self.root)
        undo = {
            "before": {i: before[i] for i in changed + deleted},
            "after": {i: after[i]["hash"] for i in created + changed},
        }
        entry = {
            "type": "command",
            "actor": actor.to_dict(),
            "command": command,
            "undo": undo,
            **(extra or {}),
        }
        seq = self._log(entry)
        return Outcome(
            "applied",
            seq=seq,
            created=_describe(self.project, created),
            changed=_describe(self.project, changed),
            deleted=[_brief(before[i]) for i in deleted],
            data=data or {},
            problems=self.project.problems(),
        )

    def _set_autonomy(self, cmd: SetAutonomy, actor: Actor) -> Outcome:
        settings = self.project.agent.model_copy(deep=True)
        if cmd.level == "act" and settings.level != "act":
            self._double_sign_off(cmd, actor)
        for key in ("level", "budget", "mcp"):
            if getattr(cmd, key) is not None:
                setattr(settings, key, getattr(cmd, key))
        self.project.agent = settings
        save_project(self.project, self.root)
        seq = self._log(
            {
                "type": "command",
                "actor": actor.to_dict(),
                "command": _dump(cmd),
                "result": settings.model_dump(mode="json"),
            }
        )
        return Outcome(
            "applied",
            seq=seq,
            data=settings.model_dump(mode="json"),
            problems=self.project.problems(),
        )

    def _double_sign_off(self, cmd: SetAutonomy, actor: Actor) -> None:
        """Unbudgeted "act" needs two separate confirmations, both in the history."""
        if cmd.confirm is None:
            token = secrets.token_hex(4)
            self._log(
                {
                    "type": "confirmation_request",
                    "actor": actor.to_dict(),
                    "token": token,
                    "command": _dump(cmd),
                }
            )
            raise PuzzleError(
                'unbudgeted "act" needs a second confirmation: resubmit with confirm=<token>',
                code="confirmation_required",
                details={"token": token},
            )
        now = datetime.now(UTC)
        entries = self.history_entries()
        used = {e["command"].get("confirm") for e in entries if e["type"] == "command"}
        for e in reversed(entries):
            if (
                e["type"] == "confirmation_request"
                and e["token"] == cmd.confirm
                and cmd.confirm not in used
                and now - datetime.fromisoformat(e["time"]) <= CONFIRM_WITHIN
            ):
                return
        raise PuzzleError(
            "that confirmation token is unknown, used or expired; ask again",
            code="confirmation_required",
        )

    def _propose(self, cmds: list[_Command], actor: Actor, rationale: str) -> Outcome:
        pid = secrets.token_hex(4)
        preview = self.preview(cmds)
        self._log(
            {
                "type": "proposal",
                "id": pid,
                "actor": actor.to_dict(),
                "rationale": rationale,
                "commands": [_dump(c) for c in cmds],
                "preview": preview,
            }
        )
        return Outcome(
            "proposed", proposal=pid, data={"preview": preview}, problems=self.project.problems()
        )

    def _open_proposal(self, proposal_id: str) -> dict[str, Any]:
        for p in self.proposals(open_only=False):
            if p["id"] == proposal_id:
                if "decision" in p:
                    raise PuzzleError(
                        f"proposal {proposal_id} is already decided", code="not_allowed"
                    )
                return p
        raise PuzzleError(f"no proposal {proposal_id}", code="not_found")

    def _run(
        self, project: Project, study: Study, cmd: RunStudy, timeout, on_output,
        on_event=None, cancel=None, *, workdir: Path | None = None, attach=None,
    ):  # fmt: skip
        from puzzle.jobs import pending
        from puzzle.results.query import summary
        from puzzle.results.store import ResultStore
        from puzzle.solve import run_study

        if workdir is None:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            workdir = self.root / WORK_DIR / f"{slug(study.name)}-{stamp}"
        on = cmd.host or (study.remote.host if study.remote is not None else None)
        context = pending.RunContext(self.root, str(study.id), workdir, on)
        with pending.running(context, attach):
            run = run_study(
                project,
                study.id,
                self.root,
                workdir,
                store=ResultStore(self.root / RESULTS_DIR),
                reuse=cmd.reuse,
                host=cmd.host,
                timeout_s=timeout,
                on_output=on_output,
                on_event=on_event,
                cancel=cancel,
            )
        data: dict[str, Any] = {
            "study": study.name,
            "solver": study.solver,
            "run_id": run.run_id,
            "cached": run.cached,
        }
        if run.points:  # a sweep (ADR 0018): each point's run
            data["points"] = [
                {"point": words, "run_id": r.run_id, "cached": r.cached} for words, r in run.points
            ]
        if not run.cached and run.job is not None and run.mesh_result is not None:
            data["duration_s"] = run.job.duration_s
            on = cmd.host or (study.remote.host if study.remote is not None else None)
            if on:  # ADR 0021: where it ran, and its cores × hours for the budget
                data["host"] = on
                data["core_hours"] = _cores_on(on, study) * run.job.duration_s / 3600
            data["mesh"] = {
                "nodes": int(len(run.mesh.node_ids)),
                "min_quality": run.mesh_result.min_quality,
                "warnings": run.mesh_result.warnings,
            }
        if run.events and not run.cached:  # how it converged, in counts (ADR 0054)
            from puzzle.progress import summary as progress_summary

            p = progress_summary(run.events)
            data["progress"] = {
                k: p[k] for k in ("increments", "cutbacks", "iterations") if p.get(k)
            }
        data["summary"] = summary(run.results)
        return run, data

    def _log(self, entry: dict[str, Any]) -> int:
        path = self.root / HISTORY
        entries = self.history_entries()
        seq = max((e.get("seq", 0) for e in entries), default=0) + 1
        entry = {"seq": seq, "time": datetime.now(UTC).isoformat(), **entry}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        return seq

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the project lock and work on the project as it is on disk."""
        lock = self.root / LOCK
        lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10.0
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > STALE_LOCK_S:
                        lock.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise PuzzleError(
                        "another client is changing the project", code="busy"
                    ) from None
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            self.project = load_project(self.root)  # another client may have changed it
            yield
        finally:
            lock.unlink(missing_ok=True)


# --- executing commands on a Project (shared by apply and preview) ----------------


def _execute(project: Project, cmd: _Command) -> dict[str, Any]:
    if isinstance(cmd, Create):
        cls = KINDS.get(cmd.kind)
        if cls is None:
            raise PuzzleError(
                f"no object kind '{cmd.kind}'", code="invalid", details={"kinds": sorted(KINDS)}
            )
        obj = _validate(cls, cmd.data, cmd.data.get("name", cmd.kind))
        if obj.id in project.objects:
            raise PuzzleError(f"an object with id {obj.id} exists", code="invalid")
        if cmd.kind == "units" and project.of_type(cls):
            raise PuzzleError(
                "the project has its units already: edit them instead", code="invalid"
            )
        project.add(obj)
        try:
            _refuse_wiring(project, obj)
        except PuzzleError:
            del project.objects[obj.id]
            raise
        return {"id": str(obj.id), "name": obj.name}
    if isinstance(cmd, Edit):
        obj = resolve(project, cmd.object)
        data = obj.model_dump(mode="json")
        for op in cmd.ops:
            _apply_op(data, op, obj.name)
        if data.get("id") != str(obj.id):
            raise PuzzleError("an object's id cannot change", code="invalid", path=obj.name)
        project.objects[obj.id] = _validate(type(obj), data, obj.name)
        try:
            _refuse_wiring(project, project.objects[obj.id])
        except PuzzleError:
            project.objects[obj.id] = obj
            raise
        renamed = data.get("name") != obj.name
        if renamed and obj.kind == "operator":  # ADR 0062: what names the parameter follows
            from puzzle import operators

            if operators.is_pre(project, project.objects[obj.id]):
                followed = operators.rename_references(project, obj.name, data["name"])
                return {"renamed_in": followed} if followed else {}
        return {}
    if isinstance(cmd, Delete):
        obj = resolve(project, cmd.object)
        users = [o.name for o in project.objects.values() if obj.id in o.dependencies()]
        if users:
            raise PuzzleError(
                f"'{obj.name}' is used by {', '.join(users)}",
                code="inconsistent",
                path=obj.name,
                details={"used_by": users},
            )
        del project.objects[obj.id]
        return {}
    if isinstance(cmd, AddLibraryMaterial):
        from puzzle.materials import add_library_material

        try:
            material = add_library_material(project, cmd.key, cmd.name)
        except KeyError as exc:
            raise PuzzleError(exc.args[0], code="not_found") from None
        return {"id": str(material.id), "name": material.name}
    if isinstance(cmd, CopyComponent):
        comp = resolve(project, cmd.component, Component)
        copy_ = project.copy_component(comp.id, cmd.name)
        out = {"id": str(copy_.id), "name": copy_.name}
        if cmd.studies:
            studies = [s for s in project.of_type(Study) if s.component == comp.id]
            made = [project.copy_study(s.id, project.unique_name(f"{s.name} ({copy_.name})"),
                                       component=copy_.id) for s in studies]  # fmt: skip
            out["studies"] = [{"id": str(s.id), "name": s.name} for s in made]
        return out
    if isinstance(cmd, CopyStudy):
        study = resolve(project, cmd.study, Study)
        copy_ = project.copy_study(study.id, cmd.name)
        return {"id": str(copy_.id), "name": copy_.name}
    raise PuzzleError(f"{cmd.command} is not applied here", code="unsupported")


def _refuse_wiring(project: Project, obj: ProjectObject) -> None:
    """An operator wired wrongly, or any reference loop (ADR 0062), is refused (the
    caller puts the object back as it was)."""
    from puzzle import operators

    if obj.kind == "operator":
        operators.refuse(project, obj)
    if obj.dependencies() or obj.kind in ("transfer", "study"):
        operators.refuse_cycles(project)


def resolve(project: Project, ref: str, cls: type[ProjectObject] = ProjectObject):
    """An object by id or by (unique) name."""
    try:
        obj = project.objects.get(UUID(ref))
    except ValueError:
        obj = None
    if obj is None:
        hits = [o for o in project.objects.values() if o.name == ref and isinstance(o, cls)]
        if len(hits) > 1:
            raise PuzzleError(
                f"{len(hits)} objects are named '{ref}'; use an id",
                code="ambiguous",
                details={"ids": [str(o.id) for o in hits]},
            )
        obj = hits[0] if hits else None
    if obj is None or not isinstance(obj, cls):
        what = getattr(cls, "kind", "object")
        names = sorted(o.name for o in project.objects.values() if isinstance(o, cls))
        raise PuzzleError(f"no {what} '{ref}'", code="not_found", details={"names": names})
    return obj


def _validate(cls: type[ProjectObject], data: dict[str, Any], where: str) -> ProjectObject:
    from pydantic import ValidationError

    from puzzle.commands.model import _errors

    try:
        return cls.model_validate(data)
    except ValidationError as exc:
        raise PuzzleError(
            f"invalid {cls.kind}", code="invalid", path=where, details={"errors": _errors(exc)}
        ) from None


def _apply_op(data: dict[str, Any], op: EditOp, where: str) -> None:
    keys = [int(k) if k.lstrip("-").isdigit() else k for k in op.path.split(".") if k]
    if not keys:
        raise PuzzleError("empty path", code="invalid", path=where)
    parent: Any = data
    try:
        for k in keys[:-1]:
            parent = parent[k]
        last = keys[-1]
        if op.op == "set":
            parent[last] = op.value
        elif op.op == "append":
            target = parent[last]
            if not isinstance(target, list):
                raise TypeError("not a list")
            target.append(op.value)
        else:
            del parent[last]
    except (KeyError, IndexError, TypeError) as exc:
        raise PuzzleError(
            f"cannot {op.op} '{op.path}' ({exc})", code="not_found", path=f"{where}: {op.path}"
        ) from None


# --- snapshots and history helpers ----------------------------------------------


def _snapshot(project: Project) -> dict[str, dict[str, Any]]:
    out = {}
    for obj in project.objects.values():
        data = obj.model_dump(mode="json")
        text = json.dumps(data, sort_keys=True)
        out[str(obj.id)] = {
            "kind": obj.kind,
            "name": obj.name,
            "data": data,
            "hash": hashlib.sha256(text.encode()).hexdigest()[:16],
        }
    return out


def _diff(before: dict, after: dict) -> tuple[list[str], list[str], list[str]]:
    created = [i for i in after if i not in before]
    deleted = [i for i in before if i not in after]
    changed = [i for i in after if i in before and after[i]["hash"] != before[i]["hash"]]
    return created, changed, deleted


def _brief(snap: dict[str, Any]) -> dict[str, str]:
    return {"id": snap["data"]["id"], "kind": snap["kind"], "name": snap["name"]}


def _describe(project: Project, ids: list[str]) -> list[dict[str, str]]:
    return [
        {"id": i, "kind": project.objects[UUID(i)].kind, "name": project.objects[UUID(i)].name}
        for i in ids
    ]


def _in_effect(entries: dict[int, dict[str, Any]]) -> set[int]:
    """Seqs of undoable entries still in effect (an undo cancels its target)."""
    live, cancelled = set(), set()
    for seq in sorted(entries, reverse=True):
        e = entries[seq]
        if e.get("undo") is None:
            continue
        if seq in cancelled:
            continue
        live.add(seq)
        if "undoes" in e:
            cancelled.add(e["undoes"])
    return live


def _dump(cmd: _Command) -> dict[str, Any]:
    return cmd.model_dump(mode="json", exclude_none=True)


def slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "study"


def _cores_on(name: str, study: Study) -> int:
    """The cores a remote run held: processes × threads, else the host's cores."""
    from puzzle import hosts

    r = study.remote
    if r is not None and r.threads:
        return r.processes * r.threads
    try:
        return hosts.get(name).cores or (r.processes if r is not None else 1)
    except PuzzleError:
        return r.processes if r is not None else 1
