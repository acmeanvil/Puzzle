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
"confirmation_request" (the first half of a double sign-off). An undo is logged as the
command "undo" and a redo as "redo", each with `undoes` (the entry taken back): a redo
is the undo of an undo, so Undo steps back through changes and Redo through undos.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
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
    RunAdapt,
    RunConvergence,
    RunMeshShootout,
    RunOptimisation,
    RunStudy,
    SetAutonomy,
    _Command,
    parse_command,
)
from puzzle.errors import Problem, PuzzleError
from puzzle.model.schema import (
    OBJECT_TYPES,
    SCHEMA_VERSION,
    Component,
    Project,
    ProjectObject,
    Study,
    group_cycle,
)
from puzzle.project import atomic, history, store
from puzzle.project.layout import LOCK, PROJECT_FILE, RESULTS_DIR, WORK_DIR
from puzzle.project.store import load_project, save_project

KINDS: dict[str, type[ProjectObject]] = {cls.kind: cls for cls in OBJECT_TYPES}
RUNS = frozenset(c.model_fields["command"].default for c in (
    RunStudy, RunOptimisation, RunConvergence, RunAdapt, RunMeshShootout))  # fmt: skip
CONFIRM_WITHIN = timedelta(minutes=15)
LEVELS = ("advise", "propose", "act_in_budget", "act")  # each may do what those before may

# Tools with an effect outside the model (files written, programs started, hosts
# reached): the least autonomy an agent needs for each (ADR 0015: "advise" changes
# nothing), and what it is, in words, for the refusal. `Session.allow` checks them.
ACTIONS = {
    "export": ("propose", "writing an export"),
    "archive": ("propose", "writing a project archive"),
    "materials_export": ("propose", "writing a material library file"),
    "operator_export": ("propose", "writing an operator's export"),
    "version_save": ("propose", "saving a version"),
    "host_check": ("propose", "reaching a remote host"),
    "jobs_live": ("propose", "asking remote hosts about jobs"),
    "job_cancel": ("act_in_budget", "cancelling a solve"),
    "pardiso_build": ("act_in_budget", "building CalculiX with PARDISO"),
}
STALE_LOCK_S = atomic.STALE_S  # another host's lock, not refreshed for this long
LOCK_WAIT_S = 10.0


@dataclass(frozen=True)
class Actor:
    kind: Literal["user", "agent", "script"]
    name: str = ""
    # ADR 0022: which model an agent ran on, and the evaluation suite it was picked by,
    # so a project's history shows which model made which change.
    model: str = ""
    suite: str = ""
    # ADR 0090 package 12: the built-in agent's turn its changes belong to, so a turn is
    # undone as one (`Session.undo_turn`); logged with the entry. `cautious` (never
    # logged) makes an agent at an "act" level propose instead of applying, after it
    # read text from outside Puzzle this turn (a solver's log, a file: UXA-11).
    turn: str = ""
    cautious: bool = field(default=False, compare=False)

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"

    def to_dict(self) -> dict[str, str]:
        out = {"kind": self.kind, "name": self.name}
        out |= {k: v for k, v in (("model", self.model), ("suite", self.suite),
                                  ("turn", self.turn)) if v}  # fmt: skip
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
        self._lock: atomic.Lock | None = None
        self._swept = False
        # BE-11: the project's files as this session last read or wrote them (a disk
        # stamp and each object's hash), so a change reads the project again only when
        # another client wrote; and the snapshot taken under the lock held now.
        self._synced: tuple[tuple, dict[str, str]] | None = None
        self._held: dict[str, dict[str, Any]] | None = None
        self.project = load_project(root)
        self._opened_checked = False  # ADR 0059: one "opened" version check per session
        if store.pending(root):  # a change a crash cut short: finish it now
            try:
                with self._locked():
                    pass
            except PuzzleError as exc:  # another client holds the lock: it finishes it,
                if exc.code != "busy":  # and the project reads through the journal
                    raise

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
            if not cmd.solves:
                return self._apply(cmd, actor)
        if isinstance(cmd, RunStudy) and cmd.resume:
            return self._resume(cmd, on_output, on_event, cancel, actor)
        return self._run_command(cmd, actor, on_output, on_event, cancel)

    def _run_command(self, cmd: _Command, actor: Actor, on_output, on_event, cancel, *,
                     workdir: Path | None = None, attach: dict[str, str] | None = None,
                     resumed: str | None = None) -> Outcome:  # fmt: skip
        """A run command (a study, an optimisation, a convergence check, adaptive passes,
        a mesh shoot-out): checked against the agent's budget before it starts and before
        each solve it makes (`puzzle.budget`), a version first, one history entry after,
        with what it spent, even when it failed or was stopped part way."""
        from puzzle import budget

        with self._locked():
            name = _run_target(self.project, cmd)
            gate = self._gate(cmd, actor)
            snapshot = copy.deepcopy(self.project)
            solved_from = self._auto_version(
                "before_run", actor, words={"study": name}, always_latest=True
            )
        # Solve outside the lock, so other clients can keep working meanwhile.
        failed: BaseException | None = None
        run, data = None, {}
        try:
            with budget.gated(gate):
                run, data = self._solve(snapshot, cmd, on_output, on_event, cancel,
                                        workdir=workdir, attach=attach)  # fmt: skip
        except BaseException as exc:  # noqa: BLE001 - logged below if it spent anything
            failed = exc
        data["spent"] = gate.spent()
        if resumed:
            data["resumed"] = resumed
        if failed is not None:
            # Refused before any solve, or a user's run: nothing to record. An agent's
            # failed or stopped run spent its solves, so its entry says what it spent.
            if not gate.solved or not actor.is_agent:
                raise failed
            data["failed"] = {
                "code": getattr(failed, "code", "error"),
                "message": str(getattr(failed, "message", failed))[:500],
            }
        with self._locked():
            seq = self._log(
                {"type": "command", "actor": actor.to_dict(), "command": _dump(cmd), "result": data}
            )
            if solved_from is not None and failed is None and gate.refused is None:
                self._mark_solved(solved_from, name)  # the state it solved from works
        if failed is not None:
            raise failed
        if gate.refused is not None:  # a runner that goes on past a refused point
            exc = gate.refused
            exc.details = {**exc.details, "solved": gate.solved, "seq": seq}
            raise exc
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
            if (actor.is_agent and self.project.agent.level != "act"
                    and self._writes_code(cmds)):  # the batch as a whole  # fmt: skip
                decisions.add("propose")
            if "propose" in decisions:
                return self._propose(cmds, actor, rationale)
            self._check_opened(actor)
            before = self._before()
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
            if cmd.solves:
                steps.append({"command": cmd.command, "would_run": _run_target(trial, cmd)})
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
        """Apply a proposal (or the listed commands of it), attributed to its author.

        Its model edits apply all or none, as one history entry and one undo, written
        with the decision in one transaction: a failure leaves the project as it was
        and the proposal open, and a proposal is never applied twice. Runs in it are
        submitted afterwards, each on its own."""
        if actor.is_agent:
            raise PuzzleError("only a user can accept a proposal", code="not_allowed")
        runs: list[_Command] = []
        outcomes = []
        with self._locked():
            proposal = self._open_proposal(proposal_id)  # read again under the lock
            author = Actor(**proposal["actor"])
            count = len(proposal["commands"])
            picks = list(range(count)) if only is None else list(only)
            if any(not isinstance(i, int) or not 0 <= i < count for i in picks):
                raise PuzzleError(f"proposal {proposal_id} has commands 0 to {count - 1}",
                                  code="invalid", details={"only": picks})  # fmt: skip
            cmds = [parse_command(proposal["commands"][i]) for i in picks]
            runs = [c for c in cmds if c.solves]
            edits = [c for c in cmds if not c.solves]
            odd = [c.command for c in edits if not c.edits_model]
            if odd:
                raise PuzzleError(f"a proposal cannot hold {', '.join(sorted(set(odd)))}",
                                  code="invalid")  # fmt: skip
            decision = {"type": "decision", "proposal": proposal_id,
                        "actor": actor.to_dict(), "accepted": picks}  # fmt: skip
            if edits:
                self._check_opened(actor)
                before = _snapshot(self.project)
                trial = copy.deepcopy(self.project)
                results = []
                for i, cmd in zip(picks, cmds, strict=True):
                    if cmd.solves:
                        continue
                    try:
                        results.append(_execute(trial, cmd))
                    except PuzzleError as exc:
                        exc.details = {**exc.details, "failed_command": i, "applied": "none"}
                        raise
                self.project = trial
                if len(edits) == 1:
                    dumped, data = _dump(edits[0]), results[0]
                else:
                    dumped = {"command": "batch", "commands": [_dump(c) for c in edits]}
                    data = {"steps": results}
                extra: dict[str, Any] = {"result": data} if data else {}
                extra |= {"approved_by": actor.to_dict(), "proposal": proposal_id}
                outcomes.append(self._record(dumped, author, before, extra=extra, data=data,
                                             also=[decision]))  # fmt: skip
                self._edits_version(actor)
            else:
                self._log(decision)
        for cmd in runs:
            outcomes.append(self.submit(cmd, actor))
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

    def undo_target(self, actor: Actor = USER) -> int | None:
        """The entry Undo would take back: the latest change in effect that is not
        itself an undo (a change or a redo), so Undo pressed twice steps back twice."""
        entries = {e["seq"]: e for e in self.history_entries() if "seq" in e}
        live, depth = _in_effect(entries), _depths(entries)
        found = [s for s, e in entries.items() if s in live and e.get("undo")
                 and depth[s] % 2 == 0 and _mine(e, actor)]  # fmt: skip
        return max(found) if found else None

    def redo_target(self, actor: Actor = USER) -> int | None:
        """The undo Redo would take back: the latest undo in effect made since the last
        fresh change (a new change ends what can be redone, as in every editor)."""
        entries = {e["seq"]: e for e in self.history_entries() if "seq" in e}
        live, depth = _in_effect(entries), _depths(entries)
        fresh = max((s for s, e in entries.items() if e.get("undo") and depth[s] == 0),
                    default=0)  # fmt: skip
        found = [s for s, e in entries.items() if s in live and s > fresh and e.get("undo")
                 and depth[s] % 2 == 1 and _mine(e, actor)]  # fmt: skip
        return max(found) if found else None

    def redo(self, actor: Actor = USER) -> Outcome:
        """Take back the latest undo (ADR 0090 UX-4): its entry is undone, which puts the
        change back; one history entry, itself undoable."""
        with self._locked():
            if actor.is_agent and self.project.agent.level == "advise":
                raise PuzzleError("agents only advise in this project", code="not_allowed")
            seq = self.redo_target(actor)
            if seq is None:
                raise PuzzleError("nothing to redo", code="not_undoable")
            return self._undo(actor, seq, "redo")

    def undo(self, actor: Actor = USER, *, seq: int | None = None) -> Outcome:
        """Undo one applied entry (default: the latest change or redo, `undo_target`).
        Undoing an undo by its `seq` redoes it (`redo` picks that one).

        Agents may undo only their own changes, and only above "advise".
        """
        with self._locked():
            if actor.is_agent and self.project.agent.level == "advise":
                raise PuzzleError("agents only advise in this project", code="not_allowed")
            return self._undo(actor, seq, "undo")

    def _undo(self, actor: Actor, seq: int | None, name: str) -> Outcome:
        """Undo, under the lock the caller holds; `name` is the history's command."""
        entries = {e["seq"]: e for e in self.history_entries() if "seq" in e}
        live = _in_effect(entries)
        if seq is None:
            seq = self.undo_target(actor)
            if seq is None:
                raise PuzzleError("nothing to undo", code="not_undoable")
        entry = entries.get(seq)
        if entry is None:
            raise PuzzleError(f"no history entry {seq}", code="not_found")
        if not entry.get("undo") or seq not in live:
            raise PuzzleError(f"entry {seq} cannot be undone", code="not_undoable")
        if actor.is_agent and not _same_actor(entry["actor"], actor):
            raise PuzzleError("agents may undo only their own changes", code="not_allowed")
        state = entry["undo"]
        current = _snapshot(self.project)
        stale = [i for i, h in state["after"].items() if (current.get(i) or {}).get("hash") != h]
        if stale:
            names = [current[i]["name"] if i in current else i for i in stale]
            raise PuzzleError(
                "changed since; undo the later changes first",
                code="not_undoable",
                details={"objects": names},
            )
        before = current
        staged = None
        if (entry.get("command") or {}).get("command") == "restore_version" and entry.get(
            "files_from"
        ):  # a restore's files go back too (ADR 0059)
            from puzzle.project import versions

            staged = versions.stage_restore(self.root, versions.get(self.root, entry["files_from"]))
        try:
            trial = (store.load_staged(self.root, staged) if staged is not None
                     else copy.deepcopy(self.project))  # fmt: skip
            from puzzle.project.migrations import upgrade_object

            found = entry.get("schema", 1)
            for i in state["after"]:
                trial.objects.pop(UUID(i), None)
            for snap in state["before"].values():
                kind, data = upgrade_object(snap["kind"], snap["data"], found)
                obj = KINDS[kind].model_validate(data)
                trial.objects[obj.id] = obj
            _refuse_dangling(self.project, trial)
            self._refuse_new_code(trial, actor, "undo")
        except BaseException:
            if staged is not None:
                staged.discard()
            raise
        self.project = trial
        return self._record(
            {"command": name, "seq": seq}, actor, before, extra={"undoes": seq},
            staged=staged,
        )  # fmt: skip

    def turn_changes(self, turn: str) -> list[int]:
        """The seqs of an agent turn's changes still in effect, oldest first (ADR 0090
        package 12): what Undo this turn takes back. Runs are kept: they change no model."""
        entries = {e["seq"]: e for e in self.history_entries() if "seq" in e}
        live, depth = _in_effect(entries), _depths(entries)
        return sorted(s for s, e in entries.items() if s in live and e.get("undo")
                      and depth[s] % 2 == 0
                      and (e.get("actor") or {}).get("turn") == turn)  # fmt: skip

    def undo_turn(self, turn: str, actor: Actor = USER) -> Outcome:
        """Undo every change an agent's turn made, newest first, as one history entry
        (`undoes_turn`), so Redo puts the whole turn back. Refused, naming them, when a
        later change touched the same objects: undo that first. Agents may undo only
        their own turns."""
        with self._locked():
            if actor.is_agent and self.project.agent.level == "advise":
                raise PuzzleError("agents only advise in this project", code="not_allowed")
            entries = {e["seq"]: e for e in self.history_entries() if "seq" in e}
            seqs = self.turn_changes(turn)
            if not seqs:
                raise PuzzleError("nothing of that turn is left to undo", code="not_undoable",
                                  details={"turn": turn})  # fmt: skip
            if actor.is_agent and not all(_same_actor(entries[s]["actor"], actor) for s in seqs):
                raise PuzzleError("agents may undo only their own changes", code="not_allowed")
            if any((entries[s].get("command") or {}).get("command") == "restore_version"
                   for s in seqs):  # fmt: skip
                raise PuzzleError("this turn restored a version: undo that on its own first",
                                  code="not_undoable")  # fmt: skip
            from puzzle.project.migrations import upgrade_object

            before = _snapshot(self.project)
            trial = copy.deepcopy(self.project)
            for seq in reversed(seqs):
                entry = entries[seq]
                state = entry["undo"]
                now = _snapshot(trial)
                stale = [
                    i for i, h in state["after"].items() if (now.get(i) or {}).get("hash") != h
                ]
                if stale:
                    names = [now[i]["name"] if i in now else i for i in stale]
                    raise PuzzleError("changed since the turn; undo the later changes first",
                                      code="not_undoable", details={"objects": names})  # fmt: skip
                for i in state["after"]:
                    trial.objects.pop(UUID(i), None)
                for snap in state["before"].values():
                    kind, data = upgrade_object(snap["kind"], snap["data"], entry.get("schema", 1))
                    obj = KINDS[kind].model_validate(data)
                    trial.objects[obj.id] = obj
            _refuse_dangling(self.project, trial)
            self._refuse_new_code(trial, actor, "undo")
            self.project = trial
            return self._record({"command": "undo_turn", "turn": turn, "seqs": seqs}, actor,
                                before, extra={"undoes_turn": seqs})  # fmt: skip

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
            staged = versions.stage_restore(self.root, target)  # every blob checked first
            try:
                restored = store.load_staged(self.root, staged)  # it must open
                self._refuse_new_code(restored, actor, "restore")
                kept = self._auto_version(
                    "before_restore", actor, words={"version": target.number},
                    always_latest=True, forced=True,
                )  # fmt: skip
                if kept is None:
                    raise PuzzleError(
                        "the project as it is now could not be kept as a version (is the "
                        "disk full or read-only?), so nothing was restored",
                        code="write_failed",
                    )
                before = _snapshot(self.project)
            except BaseException:
                staged.discard()
                raise
            self.project = restored
            return self._record(
                {"command": "restore_version", "version": target.number}, actor, before,
                extra={"restored": target.number, "files_from": kept.number,
                       "files": staged.changes()},
                staged=staged,
            )  # fmt: skip

    def mark_version(self, number: int, good: bool = True, actor: Actor = USER):
        from puzzle.project import versions

        if actor.is_agent and self.project.agent.level == "advise":
            raise PuzzleError("agents only advise in this project", code="not_allowed")
        with self._locked():
            return versions.mark(self.root, number, good, "marked by you" if good else "")

    def _last_seq(self) -> int:
        return history.last_seq(self.root)

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
        """Every entry that reads; a torn or damaged line is skipped (BE-5): the next
        append sets a torn end aside, and a damaged one is the problem
        `damaged_history`."""
        return history.entries(self.root)

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
        """Fresh agent runs, solver seconds and remote core-hours since autonomy was last
        set, from the history: each run command's entry says what it spent (`spent`:
        every solve it made, counted by `puzzle.budget`, failed or stopped ones too)."""
        runs, seconds, core_hours = 0, 0.0, 0.0
        for e in self.history_entries():
            command = e.get("command") or {}
            name = command.get("command", "")
            if e["type"] != "command":
                continue
            if name == "set_autonomy":
                runs, seconds, core_hours = 0, 0.0, 0.0
                continue
            if (e.get("actor") or {}).get("kind") != "agent" or name not in RUNS:
                continue
            result = e.get("result") or {}
            spent = result.get("spent")
            if spent is not None:
                runs += spent.get("solves", 0)
                seconds += spent.get("solver_seconds", 0.0)
                core_hours += spent.get("core_hours", 0.0)
            elif not result.get("cached"):  # an entry written before `spent` was kept
                runs += result.get("solved", 1)  # a sweep: its fresh points
                seconds += result.get("duration_s", 0.0)
                core_hours += result.get("core_hours", 0.0)
        return {"runs": runs, "solver_seconds": seconds, "core_hours": core_hours}

    def allow(self, actor: Actor, action: str) -> None:
        """May `actor` take `action` now (a tool with an effect outside the model:
        `ACTIONS`)? Users may; an agent needs the action's level or above, else
        `not_allowed`. Runs are not actions: they are run commands (`submit`)."""
        if not actor.is_agent:
            return
        least, words = ACTIONS[action]
        level = self.project.agent.level
        if LEVELS.index(level) < LEVELS.index(least):
            needs = {"propose": "'propose' or above", "act_in_budget": "'act within the "
                     "budget' or 'act'"}[least]  # fmt: skip
            raise PuzzleError(
                f"{words} needs autonomy {needs}; this project is at '{level}': ask the user",
                code="not_allowed",
                details={"action": action, "level": level, "needs": least},
            )

    def refresh(self) -> Project:
        """The project as it is on disk now (another client may have changed it), for a
        long-lived session's reads; changes reload it under the lock anyway."""
        self.project = load_project(self.root)
        return self.project

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
        if actor.cautious:  # it read untrusted text this turn: the user reviews (UXA-11)
            return "propose"
        if level != "act" and self._writes_code([cmd]):  # ADR 0062: new code is proposed
            return "propose"
        return "apply"

    def _writes_code(self, cmds: list[_Command]) -> bool:
        """Whether model edits would put code that runs into the project (a script, a
        custom PDE's weak form): below "act" an agent proposes it, the user accepts.
        Decided by what they do (ADR 0090, BE-9): applied to a copy, its code compared
        with the project's, so no path or shape of value goes round it. Edits that
        fail on the copy fail when applied too: nothing to propose."""
        from puzzle import trust

        edits = [c for c in cmds if c.edits_model]
        if not edits:
            return False
        trial = copy.deepcopy(self.project)
        try:
            for cmd in edits:
                _execute(trial, cmd)
        except PuzzleError:
            return False
        return bool(trust.new_code(self.project, trial))

    def _refuse_new_code(self, after: Project, actor: Actor, what: str) -> None:
        """An undo or a restore an agent below "act" asks for, that would bring code
        back into the project: the user does it (neither can be proposed)."""
        from puzzle import trust

        if not actor.is_agent or self.project.agent.level == "act":
            return
        found = trust.new_code(self.project, after)
        if found:
            raise PuzzleError(
                f"this {what} would bring back {', '.join(sorted(set(found)))}: code is "
                "the user's to accept, so ask them to do it",
                code="not_allowed",
                details={"code": found},
            )

    def _gate(self, cmd: _Command, actor: Actor):
        """The budget gate for a run command: an agent at "act_in_budget" is refused up
        front when its runs, solver time or (for a remote run) core-hours are spent, or
        a sweep would overrun them; then each solve the command makes is checked again
        (`puzzle.budget`). Everyone else's solves are only counted."""
        from puzzle import budget

        study = None if isinstance(cmd, RunOptimisation) else self._study_of(cmd)

        def cores(host: str) -> int:
            return _cores_on(host, study) if study is not None else 1

        if not actor.is_agent or self.project.agent.level != "act_in_budget":
            return budget.Gate(cores=cores)
        allowed, used = self.project.agent.budget, self.budget_used()
        host = getattr(cmd, "host", None) or getattr(getattr(study, "remote", None), "host",
                                                    None)  # fmt: skip
        self._refuse_spent(allowed, used, remote=bool(host))
        if isinstance(cmd, RunStudy):
            fresh = self._fresh_points(cmd)
            if used["runs"] + fresh > allowed.runs:  # a sweep would overrun it part way
                raise PuzzleError(
                    f"the sweep would solve {fresh} points and the agent has "
                    f"{allowed.runs - used['runs']:g} runs left; propose the run instead",
                    code="budget_exceeded",
                    details={"budget": allowed.model_dump(), "used": used, "points": fresh},
                )

        def check(gate, remote: bool) -> float:
            now = {"runs": used["runs"] + gate.solved,
                   "solver_seconds": used["solver_seconds"] + gate.seconds,
                   "core_hours": used["core_hours"] + gate.core_hours}  # fmt: skip
            self._refuse_spent(allowed, now, remote=remote)
            left = allowed.solver_seconds - now["solver_seconds"]
            asked = getattr(cmd, "timeout_s", None)
            return min(asked, left) if asked else left

        return budget.Gate(check=check, cores=cores)

    @staticmethod
    def _refuse_spent(allowed, used: dict, *, remote: bool) -> None:
        if remote and allowed.core_hours - used["core_hours"] <= 0:  # ADR 0021: can cost money
            raise PuzzleError(
                "the agent's remote core-hours are spent; propose the run instead",
                code="budget_exceeded",
                details={"budget": allowed.model_dump(), "used": used},
            )
        if used["runs"] >= allowed.runs or allowed.solver_seconds - used["solver_seconds"] <= 0:
            raise PuzzleError(
                "the agent's run budget is spent; propose the run instead",
                code="budget_exceeded",
                details={"budget": allowed.model_dump(), "used": used},
            )

    def _study_of(self, cmd: _Command) -> Study:
        if isinstance(cmd, RunConvergence):
            from puzzle import convergence as conv

            return conv.settings(self.project, cmd.study)[0]
        return resolve(self.project, cmd.study, Study)

    def _fresh_points(self, cmd: RunStudy) -> int:
        """How many runs a study would solve now: 1, or a sweep's points not already in
        the store (reused points cost nothing)."""
        from puzzle import sweep
        from puzzle.project.graph import run_hashes
        from puzzle.results.store import ResultStore

        study = resolve(self.project, cmd.study, Study)
        if sweep.run_count(study) <= 1:
            return 1
        try:
            keys = run_hashes(self.project)[study.id][study.solver]
        except Exception:  # noqa: BLE001 - not countable yet: count every point
            return sweep.run_count(study)
        store = ResultStore(self.root / RESULTS_DIR)
        if not cmd.reuse:
            return len(keys)
        try:
            return sum(store.find_run(k) is None for k in keys)
        except Exception:  # noqa: BLE001 - no store yet (or a stale one): all are new
            return len(keys)

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
        before = self._before()
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
        also: list[dict[str, Any]] | None = None,
        staged=None,
    ) -> Outcome:
        """Save the change and its history entry (and `also`, further entries) as one
        transaction; `staged` holds a restore's files, written in the same one."""
        after = _snapshot(self.project)
        created, changed, deleted = _diff(before, after)
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
        entry["schema"] = SCHEMA_VERSION  # the shape its undo snapshots are in
        seqs = self._save(
            [entry, *(also or [])],
            only=[UUID(i) for i in created + changed],
            gone=[(before[i]["kind"], i) for i in deleted],
            staged=staged,
        )
        if staged is None:
            self._written(after, created + changed)
        return Outcome(
            "applied",
            seq=seqs[0],
            created=_describe(self.project, created),
            changed=_describe(self.project, changed),
            deleted=[_brief(before[i]) for i in deleted],
            data=data or {},
            problems=self.project.problems(),
        )

    def _save(self, entries: list[dict[str, Any]], *, only=None, gone=(), staged=None
              ) -> list[int]:  # fmt: skip
        """Write the project's changed files with `entries` in one transaction, under the
        lock this session still holds. A project read at an older schema keeps a
        version of its old files first, then is written whole at the new one."""
        self._synced = None  # set again by `_written` when it knows the files
        if self.project.migrated_from is not None:
            from puzzle.project import versions

            versions.save(
                self.root, how="auto", trigger="migrated", project=self.project,
                words={"found": self.project.migrated_from}, history_seq=self._last_seq(),
            )  # fmt: skip
            only, gone = None, ()
        if staged is not None:
            staged.check()
        # A failure before the commit point removes the staged files (commit does);
        # after it they are the change, finished by the next open: never discarded here.
        seqs = save_project(self.project, self.root, only=only, gone=gone, entries=entries,
                            check=self._check_lock, staged=staged)  # fmt: skip
        self.project.migrated_from = None
        return seqs

    def _before(self) -> dict[str, dict[str, Any]]:
        """The project's snapshot before a change: the one taken under this lock when
        the project was kept, else taken now."""
        held, self._held = self._held, None  # good for one change: the one about to be
        return held if held is not None else _snapshot(self.project)

    def _written(self, after: dict[str, dict[str, Any]], ids: list[str]) -> None:
        """After this session wrote a change: the objects written put back as their
        files read (so the project kept equals one read afresh), and the disk as it is
        now remembered, so the next change needn't read the project again (BE-11)."""
        try:
            store.reread(self.project, self.root, [UUID(i) for i in ids])
        except (OSError, PuzzleError):
            return  # read again under the next lock
        hashes = {i: e["hash"] for i, e in after.items()}
        if ids:
            hashes.update({i: e["hash"] for i, e in
                           _snapshot(self.project, [UUID(i) for i in ids]).items()})  # fmt: skip
        self._synced = (store.disk_stamp(self.root), hashes)

    def _sync(self) -> None:
        """Under the lock: the project as it is on disk. Kept when no project file was
        written since this session's own change and nothing in memory differs from
        it; read again otherwise (another client may have changed it)."""
        self._held = None
        synced, self._synced = self._synced, None
        if synced is not None and self.project.migrated_from is None:
            stamp = store.disk_stamp(self.root)
            if stamp == synced[0]:
                snap = _snapshot(self.project)
                if {i: e["hash"] for i, e in snap.items()} == synced[1]:
                    from puzzle import trust

                    self.project.trusted = trust.is_trusted(self.root)
                    self._held, self._synced = snap, synced
                    return
        self.project = load_project(self.root)

    def _check_lock(self) -> None:
        if self._lock is None:
            raise PuzzleError("the project is written only under its lock", code="lock_lost")
        self._lock.check()

    def _set_autonomy(self, cmd: SetAutonomy, actor: Actor) -> Outcome:
        settings = self.project.agent.model_copy(deep=True)
        if cmd.level == "act" and settings.level != "act":
            self._double_sign_off(cmd, actor)
        for key in ("level", "budget", "mcp"):
            if getattr(cmd, key) is not None:
                setattr(settings, key, getattr(cmd, key))
        self.project.agent = settings
        (seq,) = self._save(
            [{"type": "command", "actor": actor.to_dict(), "command": _dump(cmd),
              "result": settings.model_dump(mode="json")}],
            only=[],
        )  # fmt: skip
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

    def _solve(self, project: Project, cmd: _Command, on_output, on_event, cancel, *,
               workdir: Path | None = None, attach=None):  # fmt: skip
        """Run `cmd` on `project` (a copy): what it made, for in-process clients (the
        StudyRun, the points, the report), and what goes into its history entry."""
        if isinstance(cmd, RunStudy):
            study = resolve(project, cmd.study, Study)
            return self._run(project, study, cmd, cmd.timeout_s, on_output, on_event, cancel,
                             workdir=workdir, attach=attach)  # fmt: skip
        from puzzle.results.store import ResultStore

        store = ResultStore(self.root / RESULTS_DIR)
        work = self.root / WORK_DIR
        if isinstance(cmd, RunOptimisation):
            from puzzle import optimise
            from puzzle.model.schema import Optimisation

            opt = resolve(project, cmd.optimisation, Optimisation)
            pts = optimise.run(project, opt, self.root, work / "optimise" / str(opt.id),
                               store=store, budget=cmd.budget, on_output=on_output,
                               cancel=cancel)  # fmt: skip
            return pts, {"optimisation": opt.name, "points": len(pts), "ended": pts.ended}
        if isinstance(cmd, RunConvergence):
            from puzzle import convergence as conv

            st, c = conv.settings(project, cmd.study)
            goal = cmd.target or c.target
            lv = conv.run(project, st, self.root, work / "convergence" / str(st.id),
                          store=store, count=cmd.levels or c.levels, ratio=cmd.ratio or c.ratio,
                          outputs=c.quantities, on_output=on_output, cancel=cancel)  # fmt: skip
            out = conv.report(lv, conv.verdicts(lv, goal), goal)
            return out, {"study": st.name, "levels": len(lv), "converged": out["converged"]}
        if isinstance(cmd, RunAdapt):
            from puzzle import adapt
            from puzzle.model.schema import Adapt

            st = resolve(project, cmd.study, Study)
            a = st.adapt or Adapt()
            changes = {k: v for k, v in (("target", cmd.target), ("passes", cmd.passes),
                                         ("method", cmd.method)) if v}  # fmt: skip
            a = Adapt.model_validate({**a.model_dump(), **changes})
            out = adapt.run(project, st, self.root, work / "adapt" / str(st.id), store=store,
                            settings=a, on_output=on_output, cancel=cancel)  # fmt: skip
            return out, {"study": st.name, "passes": len(out.passes), "ended": out.ended}
        if isinstance(cmd, RunMeshShootout):
            import tempfile

            from puzzle.shootout import mesh_shootout_report

            st = resolve(project, cmd.study, Study)
            with tempfile.TemporaryDirectory(prefix="puzzle-shootout-") as tmp:
                out = mesh_shootout_report(project, st.id, self.root, Path(tmp),
                                           cmd.quantities, cmd.tolerance,
                                           on_output=on_output)  # fmt: skip
            return out, {"study": st.name, "passed": out["passed"]}
        raise PuzzleError(f"{cmd.command} is not a run", code="unsupported")

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
            workdir = fresh_folder(self.root / WORK_DIR / f"{slug(study.name)}-{stamp}")
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
        if run.points:  # a sweep spends a run per point it solves, not one for them all
            fresh = [r for _, r in run.points if not r.cached]
            data["solved"] = len(fresh)
            data["duration_s"] = sum(r.job.duration_s for r in fresh if r.job is not None)
            if on:
                data["core_hours"] = _cores_on(on, study) * data["duration_s"] / 3600
        if run.events and not run.cached:  # how it converged, in counts (ADR 0054)
            from puzzle.progress import summary as progress_summary

            p = progress_summary(run.events)
            data["progress"] = {
                k: p[k] for k in ("increments", "cutbacks", "iterations") if p.get(k)
            }
        data["summary"] = summary(run.results)
        return run, data

    def _log(self, entry: dict[str, Any]) -> int:
        """Append one entry that changes no file (a proposal, a decision, a run)."""
        return store.commit(self.root, {}, [], [entry], check=self._check_lock)[0]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the project lock and work on the project as it is on disk: a change a
        crash cut short is finished first (`store.recover`)."""
        lock = atomic.Lock(self.root / LOCK, what="the project")
        lock.acquire(LOCK_WAIT_S)
        try:
            self._lock = lock
            store.recover(self.root)
            if not self._swept:  # temporary files a crash left before committing
                self._swept = True
                store.sweep(self.root)
            self._sync()  # another client may have changed it
            yield
        finally:
            self._lock = None
            self._held = None
            lock.release()


# --- executing commands on a Project (shared by apply and preview) ----------------


def _execute(project: Project, cmd: _Command) -> dict[str, Any]:
    if isinstance(cmd, Create):
        cls = KINDS.get(cmd.kind)
        if cls is None:
            raise PuzzleError(
                f"no object kind '{cmd.kind}'", code="invalid", details={"kinds": sorted(KINDS)}
            )
        data = cmd.data
        new_table = cmd.kind == "operator" and data.get("type") == "result_table"
        if new_table and data.get("layout") in (None, "setting"):
            from puzzle.settings import current

            # ADR-E-7: the layout a new table takes is the user's preference, made the
            # operator's own now; it is never read again when the table is worked out.
            data = {**data, "layout": current().get("results.table_layout")}
        obj = _validate(cls, data, cmd.data.get("name", cmd.kind))
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
        if obj.kind == "operator" and data.get("layout") == "setting":
            data["layout"] = "long"  # ADR-E-7: as it has been read since, now its own
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


def _run_target(project: Project, cmd: _Command) -> str:
    """The study (or optimisation) a run command names, which must exist."""
    from puzzle.model.schema import Optimisation

    if isinstance(cmd, RunOptimisation):
        return resolve(project, cmd.optimisation, Optimisation).name
    if isinstance(cmd, RunConvergence):
        from puzzle import convergence as conv

        return conv.settings(project, cmd.study)[0].name
    return resolve(project, cmd.study, Study).name


def _refuse_wiring(project: Project, obj: ProjectObject) -> None:
    """An operator wired wrongly, or any reference loop (ADR 0062), is refused (the
    caller puts the object back as it was)."""
    from puzzle import operators

    if obj.kind == "operator":
        operators.refuse(project, obj)
    if obj.dependencies() or obj.kind in ("transfer", "study"):
        operators.refuse_cycles(project)
    if obj.kind == "group":
        _refuse_group_cycles(project, obj)


def _refuse_group_cycles(project: Project, obj: ProjectObject) -> None:
    loop = group_cycle(project)
    if loop:
        names = [g.name for g in loop]
        moved = obj.name if obj in loop else names[0]
        raise PuzzleError(
            f"'{moved}' can't go inside a group it holds: " + " → ".join([*names, names[0]]),
            code="group_cycle",
            path=obj.name,
            details={"groups": names},
        )


def _dangling(project: Project) -> set[tuple[UUID, UUID]]:
    return {(o.id, d) for o in project.objects.values() for d in o.dependencies()
            if d not in project.objects}  # fmt: skip


def _refuse_dangling(current: Project, trial: Project) -> None:
    """An undo that would leave an object naming one it removes (or that a restored
    object names, gone since) is refused, as Delete refuses it (BE-8)."""
    new = _dangling(trial) - _dangling(current)
    if not new:
        return
    users = sorted({trial.objects[o].name for o, _ in new})

    def name(i: UUID) -> str:
        obj = current.objects.get(i)
        return obj.name if obj is not None else str(i)

    gone = sorted({name(d) for _, d in new})
    raise PuzzleError(
        f"{', '.join(users)} {'uses' if len(users) == 1 else 'use'} {', '.join(gone)}, which "
        "this undo would leave missing: undo the later changes first",
        code="not_undoable",
        details={"objects": users, "missing": gone},
    )


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
        errors = _errors(exc)
        first = errors[0] if errors else None  # OP-15: the reason, so it can be put right
        why = (f": {first['path'] + ': ' if first['path'] else ''}{first['message']}"
               if first else "")  # fmt: skip
        raise PuzzleError(
            f"invalid {cls.kind}{why}", code="invalid", path=where, details={"errors": errors}
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


def _snapshot(project: Project, ids=None) -> dict[str, dict[str, Any]]:
    out = {}
    objs = (
        project.objects.values()
        if ids is None
        else (project.objects[i] for i in ids if i in project.objects)
    )
    for obj in objs:
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


def _depths(entries: dict[int, dict[str, Any]]) -> dict[int, int]:
    """How many undos deep each entry is: a change 0, its undo 1, a redo 2, ... Even
    ones put something into effect, odd ones take it back."""
    depth: dict[int, int] = {}
    for seq in sorted(entries):
        target = entries[seq].get("undoes")
        if target is not None:
            depth[seq] = depth.get(target, 0) + 1
        else:  # a turn undone (package 12) takes its changes back: an undo's depth
            depth[seq] = 1 if entries[seq].get("undoes_turn") else 0
    return depth


def _mine(entry: dict[str, Any], actor: Actor) -> bool:
    """An agent's undo and redo reach its own entries only."""
    return not actor.is_agent or _same_actor(entry["actor"], actor)


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
        cancelled.update(e.get("undoes_turn") or ())  # a turn undone as one (package 12)
    return live


def _dump(cmd: _Command) -> dict[str, Any]:
    return cmd.model_dump(mode="json", exclude_none=True)


def fresh_folder(base: Path) -> Path:
    """A run folder made now and nobody else's (BE-20: two runs started in one second,
    the GUI's and an agent's, wrote into one folder): `base`, else `base-2`, `-3`..."""
    base.parent.mkdir(parents=True, exist_ok=True)
    for n in range(1, 10_000):
        folder = base if n == 1 else base.with_name(f"{base.name}-{n}")
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder
    raise PuzzleError(f"no free run folder beside {base}", code="write_failed")


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
