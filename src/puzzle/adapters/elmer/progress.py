"""Elmer's progress (ADR 0054): time steps (`MAIN: Time: k/n: t`; a harmonic scan
prints the same, one frequency each, counted k of n), each solver's nonlinear
iterations (`ComputeChange: NS (ITER=i) (NRM,RELC): (norm change) :: name`, the
relative change against the SIF's `Nonlinear System Convergence Tolerance`), and
`WARNING::`/`ERROR::` lines.

Trackers read the `.vtu` files Elmer writes into the mesh folder as it goes, each one
once the next has appeared (or at the end), mapped to Puzzle's nodes by the adapter's
node map, so a tracker's value is the one the stored results give."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from puzzle.progress import (
    Converged,
    Diverged,
    Event,
    Increment,
    Iteration,
    Message,
    Parser,
    Resource,
    Step,
    Tracked,
)

_NUM = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
_TIME = re.compile(r"MAIN:\s+Time:\s*(\d+)\s*/\s*(\d+)\s*:\s*" + _NUM)
_CHANGE = re.compile(
    r"ComputeChange:\s+(NS|SS)\s+\(ITER=(\d+)\)\s+\(NRM,RELC\):\s+\(\s*" + _NUM + r"\s+" + _NUM
    + r"\s*\)\s*::\s*(.+)"
)  # fmt: skip
_TOLERANCE = re.compile(r"Nonlinear System Convergence Tolerance\s*=\s*" + _NUM, re.IGNORECASE)
_DOFS = {"heatsolve": 1, "statelecsolve": 1, "stresssolve": None}  # None: one per axis


class ElmerProgress(Parser):
    def __init__(self, input_file: Path, step: int = 1, of: int = 1) -> None:
        super().__init__(step, of)
        from puzzle.adapters.elmer.sif import MESH_DIR, OUTPUT, TIMES

        self.folder = input_file.parent / MESH_DIR
        self.pattern = f"{OUTPUT}*.vtu"
        self.times_file = self.folder / TIMES
        sif = (
            input_file.read_text(errors="replace", encoding="utf-8") if input_file.exists() else ""
        )
        self.transient = re.search(r"Simulation Type\s*=\s*Transient", sif, re.I) is not None
        sizes = re.search(r"Timestep Sizes\(\d+\)\s*=\s*" + _NUM, sif)
        counts = re.search(r"Timestep Intervals\(\d+\)\s*=\s*(\d+)", sif)
        self.end_time = (
            float(sizes.group(1)) * int(counts.group(1)) if self.transient and sizes and counts
            else None
        )  # fmt: skip
        tolerances = [float(v) for v in _TOLERANCE.findall(sif)]
        self.tolerance = min(tolerances) if tolerances else None
        planar = re.search(r"Coordinate System\s*=\s*\"?(Cartesian 2D|Axi Symmetric)", sif, re.I)
        self.dofs = self._dofs(sif.lower(), 2 if planar else 3)
        self.started = False
        self.increment = 0
        self.iteration = 0
        self.read: set[str] = set()
        self.times: list[float] = []
        self.scanning = False

    def _dofs(self, sif: str, dim: int) -> int | None:
        for word, n in _DOFS.items():
            if word in sif:
                return n or dim
        return None

    def _start(self) -> list[Event]:
        if self.started:
            return []
        self.started = True
        out: list[Event] = [
            Step(
                number=self.step,
                of=self.of,
                type="transient" if self.transient else "",
                end_time=self.end_time,
            )
        ]
        header = self.folder / "mesh.header"
        if self.dofs and header.exists():
            nodes = int(header.read_text(encoding="utf-8").split()[0])
            out.append(Resource(equations=nodes * self.dofs))
        out += [
            Message(
                level="info",
                text=f"Not tracked: {t.name}: Elmer follows displacement "
                "and temperature only while solving",
            )
            for t in self.trackers
            if t.field not in ("displacement", "temperature")
        ]
        return out

    def _converged(self) -> list[Event]:
        if not self.increment:
            return []
        time = self._time(self.increment)
        return [Converged(step=self.step, increment=self.increment, iterations=self.iteration,
                          time=time)]  # fmt: skip

    def _time(self, k: int) -> float | None:
        if self.scanning:
            return float(k)
        return self.times[k - 1] if self.transient and k <= len(self.times) else None

    def feed(self, line: str) -> list[Event]:
        out = self._start()
        text = line.strip()
        if m := _TIME.search(text):
            out += self._converged()
            k, n, t = int(m.group(1)), int(m.group(2)), float(m.group(3))
            self.increment, self.iteration = k, 0
            if not self.transient:  # a scan (harmonic, one frequency each): k of n
                self.scanning = True
                out.append(Increment(step=self.step, number=k, time=float(k), end_time=float(n)))
                return out
            self.times[k - 1 : k] = [t]
            size = t - (self.times[k - 2] if k > 1 else 0.0)
            out.append(
                Increment(step=self.step, number=k, size=size, time=t, end_time=self.end_time)
            )
        elif (m := _CHANGE.search(text)) and m.group(1) == "NS":
            if not self.increment:  # steady: one increment
                self.increment = 1
                out.append(Increment(step=self.step, number=1))
            self.iteration += 1
            name = f"{m.group(5).strip()} change"
            criteria = {name: self.tolerance} if self.tolerance else {}
            out.append(
                Iteration(
                    step=self.step,
                    increment=self.increment,
                    number=self.iteration,
                    residuals={name: float(m.group(4))},
                    criteria=criteria,
                )
            )
        elif text.startswith("WARNING::"):
            body = text.removeprefix("WARNING::").strip()
            out.append(Message(level="warning", text=body, original=text, line=self.lines))
        elif text.startswith("ERROR::"):
            body = text.removeprefix("ERROR::").strip()
            out.append(Message(level="error", text=body, original=text, line=self.lines))
        elif "ALL DONE" in text:
            out += self._converged()
            self.increment = 0
        return out

    # --- trackers from the .vtu files as they appear ---------------------------------

    def poll(self) -> list[Event]:
        return self._tracked(final=False)

    def finish(self, ok: bool) -> list[Event]:
        out = self._tracked(final=True)
        if not ok:
            errors = [e for e in out if isinstance(e, Message) and e.level == "error"]
            reason = errors[-1].text if errors else "Elmer stopped"
            out.append(Diverged(step=self.step, increment=self.increment, reason=reason,
                                final=True))  # fmt: skip
        return out

    def _tracked(self, final: bool) -> list[Event]:
        if not self.trackers or not self.folder.exists():
            return []
        files = sorted(self.folder.glob(self.pattern))
        ready = files if final else files[:-1]  # the newest may still be being written
        out: list[Event] = []
        for path in ready:
            if path.name in self.read:
                continue
            self.read.add(path.name)
            out += self._values(path, files.index(path) + 1)
        return out

    def _values(self, path: Path, k: int) -> list[Event]:
        from puzzle.adapters.elmer import NODE_MAP
        from puzzle.adapters.elmer.output import read_vtu

        data = read_vtu(path)
        node_ids = np.loadtxt(self.folder / NODE_MAP, dtype=np.int64, ndmin=1)
        ids = (
            node_ids[data["GeometryIds"][:, 0].astype(np.int64) - 1]
            if "GeometryIds" in data else node_ids
        )  # fmt: skip
        time = None
        if self.transient and self.times_file.exists():
            times = np.loadtxt(self.times_file, ndmin=1)
            time = float(times[k - 1]) if k <= len(times) else None
        out: list[Event] = []
        for t in self.trackers:
            values = data.get(t.field)
            if values is None or len(values) != len(ids):
                continue
            value = t.value(ids, values)
            if value is not None:
                out.append(Tracked(name=t.name, step=self.step, increment=k, time=time,
                                   value=value, unit=t.unit))  # fmt: skip
        return out
