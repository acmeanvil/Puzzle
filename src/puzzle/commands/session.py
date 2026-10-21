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

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name}


USER = Actor("user")


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

    # --- submitting --------------------------------------------------------------

    def submit(
        self,
        command: dict[str, Any] | _Command,
        actor: Actor = USER,
        *,
        rationale: str = "",
        on_output=None,
    ) -> Outcome:
        """Apply `command` for `actor`, or record it as a proposal (agents at "propose")."""
        cmd = parse_command(command)
        with self._locked():
            decision = self._authorize(cmd, actor)
            if decision == "propose":
                return self._propose([cmd], actor, rationale)
            if not isinstance(cmd, RunStudy):
                return self._apply(cmd, actor)
            study = resolve(self.project, cmd.study, Study)
            timeout = self._run_timeout(cmd, actor)
            snapshot = copy.deepcopy(self.project)
        # Solve outside the lock, so other clients can keep working meanwhile.
        run, data = self._run(snapshot, study, cmd, timeout, on_output)
        with self._locked():
            seq = self._log(
                {"type": "command", "actor": actor.to_dict(), "command": _dump(cmd), "result": data}
            )
        return Outcome("applied", seq=seq, data=data, problems=self.project.problems(), run=run)

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
            return self._record(dumped, actor, before, extra=extra, data={"steps": results})

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
                    and (not actor.is_agent or e["actor"] == actor.to_dict())
                ]
                if not candidates:
                    raise PuzzleError("nothing to undo", code="not_undoable")
                seq = max(candidates)
            entry = entries.get(seq)
            if entry is None:
                raise PuzzleError(f"no history entry {seq}", code="not_found")
            if not entry.get("undo") or seq not in live:
                raise PuzzleError(f"entry {seq} cannot be undone", code="not_undoable")
            if actor.is_agent and entry["actor"] != actor.to_dict():
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
            for i in state["after"]:
                self.project.objects.pop(UUID(i), None)
            for snap in state["before"].values():
                obj = KINDS[snap["kind"]].model_validate(snap["data"])
                self.project.objects[obj.id] = obj
            return self._record(
                {"command": "undo", "seq": seq}, actor, before, extra={"undoes": seq}
            )

    # --- reading ---------------------------------------------------------------

    def history_entries(self) -> list[dict[str, Any]]:
        path = self.root / HISTORY
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

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
        runs, seconds = 0, 0.0
        for e in self.history_entries():
            command = e.get("command") or {}
            if e["type"] == "command" and command.get("command") == "set_autonomy":
                runs, seconds = 0, 0.0
            elif (
                e["type"] == "command"
                and command.get("command") == "run_study"
                and e["actor"]["kind"] == "agent"
                and not e.get("result", {}).get("cached")
            ):
                runs += 1
                seconds += e.get("result", {}).get("duration_s", 0.0)
        return {"runs": runs, "solver_seconds": seconds}

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
        return "apply"

    def _run_timeout(self, cmd: RunStudy, actor: Actor) -> float | None:
        if not actor.is_agent or self.project.agent.level != "act_in_budget":
            return cmd.timeout_s
        budget, used = self.project.agent.budget, self.budget_used()
        left = budget.solver_seconds - used["solver_seconds"]
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
        before = _snapshot(self.project)
        data = _execute(self.project, cmd)
        extra: dict[str, Any] = {"result": data} if data else {}
        if approved_by:
            extra |= {"approved_by": approved_by.to_dict(), "proposal": proposal}
        return self._record(_dump(cmd), actor, before, extra=extra, data=data)

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

    def _run(self, project: Project, study: Study, cmd: RunStudy, timeout, on_output):
        from puzzle.results.query import summary
        from puzzle.results.store import ResultStore
        from puzzle.solve import run_study

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        workdir = self.root / WORK_DIR / f"{slug(study.name)}-{stamp}"
        run = run_study(
            project,
            study.id,
            self.root,
            workdir,
            store=ResultStore(self.root / RESULTS_DIR),
            reuse=cmd.reuse,
            timeout_s=timeout,
            on_output=on_output,
        )
        data: dict[str, Any] = {
            "study": study.name,
            "solver": study.solver,
            "run_id": run.run_id,
            "cached": run.cached,
        }
        if not run.cached:
            data["duration_s"] = run.job.duration_s
            data["mesh"] = {
                "nodes": int(len(run.mesh.node_ids)),
                "min_quality": run.mesh_result.min_quality,
                "warnings": run.mesh_result.warnings,
            }
        data["summary"] = summary(run.results)
        return run, data

    def _log(self, entry: dict[str, Any]) -> int:
        path = self.root / HISTORY
        entries = self.history_entries()
        seq = max((e.get("seq", 0) for e in entries), default=0) + 1
        entry = {"seq": seq, "time": datetime.now(UTC).isoformat(), **entry}
        with path.open("a") as f:
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
        project.add(obj)
        return {"id": str(obj.id), "name": obj.name}
    if isinstance(cmd, Edit):
        obj = resolve(project, cmd.object)
        data = obj.model_dump(mode="json")
        for op in cmd.ops:
            _apply_op(data, op, obj.name)
        if data.get("id") != str(obj.id):
            raise PuzzleError("an object's id cannot change", code="invalid", path=obj.name)
        project.objects[obj.id] = _validate(type(obj), data, obj.name)
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
        return {"id": str(copy_.id), "name": copy_.name}
    raise PuzzleError(f"{cmd.command} is not applied here", code="unsupported")


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
