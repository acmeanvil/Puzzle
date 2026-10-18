"""Read EnSight Gold binary results (Code_Saturne's default output).

Supports what Code_Saturne writes for volume meshes: a "C Binary" geometry file with
assigned node and element ids, parts of tetra4, pyramid5, penta6, hexa8 and nfaced
(polyhedra) blocks, and per-element scalar or vector variables. Little-endian
float32 values, int32 counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NODES_PER = {"tetra4": 4, "pyramid5": 5, "penta6": 6, "hexa8": 8, "tria3": 3, "quad4": 4}


@dataclass
class Block:
    element: str
    centroids: np.ndarray  # (n, 3) mean of each element's distinct nodes


@dataclass
class Part:
    number: int
    blocks: list[Block] = field(default_factory=list)


class _Reader:
    def __init__(self, path: Path) -> None:
        self.data = path.read_bytes()
        self.pos = 0

    def text(self) -> str:
        raw = self.data[self.pos : self.pos + 80]
        self.pos += 80
        return raw.split(b"\0")[0].decode("ascii", "replace").strip()

    def ints(self, n: int) -> np.ndarray:
        out = np.frombuffer(self.data, dtype="<i4", count=n, offset=self.pos)
        self.pos += 4 * n
        return out

    def floats(self, n: int) -> np.ndarray:
        out = np.frombuffer(self.data, dtype="<f4", count=n, offset=self.pos)
        self.pos += 4 * n
        return out

    @property
    def done(self) -> bool:
        return self.pos >= len(self.data)


def read_geometry(path: Path) -> list[Part]:
    r = _Reader(path)
    if r.text() != "C Binary":
        raise ValueError(f"{path.name} is not a binary EnSight Gold file")
    r.text(), r.text()  # descriptions
    node_ids, element_ids = r.text(), r.text()
    if "given" in node_ids or "given" in element_ids:
        raise ValueError("EnSight files with given node/element ids are not supported")
    parts: list[Part] = []
    keyword = r.text()
    if keyword == "extents":
        r.floats(6)
        keyword = r.text()
    while not r.done and keyword == "part":
        part = Part(int(r.ints(1)[0]))
        r.text()  # part description
        assert r.text() == "coordinates"
        n = int(r.ints(1)[0])
        xyz = np.column_stack([r.floats(n), r.floats(n), r.floats(n)]).astype(np.float64)
        keyword = r.text() if not r.done else ""
        while keyword and keyword != "part":
            count = int(r.ints(1)[0])
            if keyword == "nfaced":
                faces_per = r.ints(count)
                nodes_per_face = r.ints(int(faces_per.sum()))
                conn = r.ints(int(nodes_per_face.sum())) - 1
                face_start = np.concatenate([[0], np.cumsum(nodes_per_face)])
                cell_face_start = np.concatenate([[0], np.cumsum(faces_per)])
                centroids = np.empty((count, 3))
                for c in range(count):
                    lo, hi = face_start[cell_face_start[c]], face_start[cell_face_start[c + 1]]
                    centroids[c] = xyz[np.unique(conn[lo:hi])].mean(axis=0)
            elif keyword in NODES_PER:
                conn = r.ints(count * NODES_PER[keyword]).reshape(count, -1) - 1
                centroids = xyz[conn].mean(axis=1)
            else:
                raise ValueError(f"EnSight element type '{keyword}' is not supported")
            part.blocks.append(Block(keyword, centroids))
            keyword = r.text() if not r.done else ""
        parts.append(part)
    return parts


def read_variable(path: Path, parts: list[Part], components: int) -> np.ndarray:
    """Per-element values, in the geometry's part and block order: (n, components)."""
    r = _Reader(path)
    r.text()  # description
    sizes = {p.number: [len(b.centroids) for b in p.blocks] for p in parts}
    chunks = []
    while not r.done:
        if r.text() != "part":
            break
        number = int(r.ints(1)[0])
        for n in sizes[number]:
            r.text()  # element type
            values = r.floats(n * components).reshape(components, n).T
            chunks.append(values.astype(np.float64))
    return np.vstack(chunks)


def read_case(case_file: Path) -> tuple[list[Part], dict[str, tuple[str, int, Path]]]:
    """Geometry and the variables listed in a .case file: name -> (kind, components, file)."""
    text = case_file.read_text()
    geo = re.search(r"model:\s*(?:\d+\s+)?(\S+)", text).group(1)
    variables = {}
    for kind, name, pattern in re.findall(
        r"(scalar|vector) per element:\s*(?:\d+\s+)?(\S+)\s+(\S+)", text
    ):
        last = sorted(case_file.parent.glob(pattern.replace("*", "?")))[-1]
        variables[name] = (kind, 1 if kind == "scalar" else 3, last)
    return read_geometry(case_file.parent / geo), variables
