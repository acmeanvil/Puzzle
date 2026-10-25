"""Mesh a BREP with Netgen for Puzzle (ADR 0019).

Runs in the netgen environment (its OpenCASCADE bindings clash with OCP's in one process),
so it must not import puzzle. Reads `case.json` beside it:

    brep: the geometry, as Gmsh wrote it (the component's parts placed and fragmented)
    maxh, grading, options: Netgen's meshing parameters
    sizes: [{dim, centre, measure, maxh}]   local sizes on faces, edges and solids
    layers: [{faces: [[x, y, z, area]], thickness: [h1, h2, ...]}]   boundary layers

and writes `mesh.npz` (first-order: points; segments, triangles, tets and prisms with
the number of the edge, face or solid they lie on, in the shape's own order, -1 for a
boundary layer's prisms) and `entities.json` (each face, edge and solid's centre and
measure, to be matched to Gmsh's entities).
"""

import json
import sys
from pathlib import Path

import numpy as np
from netgen.occ import OCCGeometry


def _signature(shape) -> list[float]:
    c = shape.center
    return [c.x, c.y, c.z, shape.mass]


def _find(shapes, centre, measure, size):
    """The shape with this centre and measure, or None."""
    best, score = None, None
    for s in shapes:
        c = s.center
        d = np.linalg.norm(np.array([c.x, c.y, c.z]) - np.asarray(centre)) / size
        m = abs(s.mass - measure) / max(abs(measure), 1e-300)
        if d < 1e-6 and m < 1e-6 and (score is None or d + m < score):
            best, score = s, d + m
    return best


def _number(name: str, prefix: str) -> int:
    """f12 -> 12; a name Netgen made itself (a boundary layer's "default") -> -1."""
    return int(name[1:]) if name[:1] == prefix and name[1:].isdigit() else -1


def main(case_path: Path) -> None:
    case = json.loads(case_path.read_text())
    here = case_path.parent
    geo = OCCGeometry(str(here / case["brep"]))
    shape = geo.shape
    lists = {1: list(shape.edges), 2: list(shape.faces), 3: list(shape.solids)}
    for i, f in enumerate(lists[2]):
        f.name = f"f{i}"
    for i, e in enumerate(lists[1]):
        e.name = f"e{i}"
    for i, s in enumerate(lists[3]):
        s.name = f"s{i}"  # becomes the domain's material name
    b = shape.bounding_box
    size = float(np.linalg.norm(np.array([b[1].x - b[0].x, b[1].y - b[0].y, b[1].z - b[0].z])))
    for s in case.get("sizes", []):
        hit = _find(lists[s["dim"]], s["centre"], s["measure"], size)
        if hit is None:
            raise SystemExit(
                f"netgen: no {['', 'edge', 'face', 'solid'][s['dim']]} at {s['centre']}"
            )
        hit.maxh = s["maxh"]
    layered = []
    for layer in case.get("layers", []):
        names = []
        for x, y, z, area in layer["faces"]:
            hit = _find(lists[2], (x, y, z), area, size)
            if hit is None:
                raise SystemExit(f"netgen: no face at {(x, y, z)} for a boundary layer")
            names.append(hit.name)
        layered.append((names, layer["thickness"]))
    geo = OCCGeometry(shape)
    params = {"maxh": case["maxh"], **case.get("options", {})}
    if layered:
        from netgen.meshing import BoundaryLayerParameters

        params["boundary_layers"] = [
            BoundaryLayerParameters(
                boundary="|".join(names), thickness=thickness, new_material="layer"
            )
            for names, thickness in layered
        ]
    mesh = geo.GenerateMesh(**params)

    points = np.array([p.p for p in mesh.Points()], dtype=float)
    segments, seg_edge = [], []
    for s in mesh.Elements1D():
        segments.append([v.nr for v in s.vertices])
        seg_edge.append(_number(mesh.GetCD2Name(s.index - 1), "e"))
    tris, tri_face, quads, quad_face = [], [], [], []
    for t in mesh.Elements2D():
        nodes = [v.nr for v in t.vertices]
        face = _number(mesh.GetBCName(t.index - 1), "f")
        if len(nodes) == 3:
            tris.append(nodes)
            tri_face.append(face)
        elif len(nodes) == 4:  # a boundary layer's side, on the face next to it
            quads.append(nodes)
            quad_face.append(face)
        else:
            raise SystemExit(f"netgen: {len(nodes)}-node faces are not read yet")
    tets, tet_solid, prisms, prism_solid = [], [], [], []
    for e in mesh.Elements3D():
        material = mesh.GetMaterial(e.index)
        solid = int(material[1:]) if material.startswith("s") else -1
        nodes = [v.nr for v in e.vertices]
        if len(nodes) == 4:
            tets.append(nodes)
            tet_solid.append(solid)
        elif len(nodes) == 6:
            prisms.append(nodes)
            prism_solid.append(solid)
        else:
            raise SystemExit(f"netgen: {len(nodes)}-node volume elements are not read yet")
    np.savez(
        here / "mesh.npz",
        points=points,
        segments=np.array(segments, dtype=np.int64).reshape(-1, 2),
        seg_edge=np.array(seg_edge, dtype=np.int64),
        tris=np.array(tris, dtype=np.int64).reshape(-1, 3),
        tri_face=np.array(tri_face, dtype=np.int64),
        quads=np.array(quads, dtype=np.int64).reshape(-1, 4),
        quad_face=np.array(quad_face, dtype=np.int64),
        tets=np.array(tets, dtype=np.int64).reshape(-1, 4),
        tet_solid=np.array(tet_solid, dtype=np.int64),
        prisms=np.array(prisms, dtype=np.int64).reshape(-1, 6),
        prism_solid=np.array(prism_solid, dtype=np.int64),
    )
    (here / "entities.json").write_text(
        json.dumps({str(d): [_signature(s) for s in shapes] for d, shapes in lists.items()})
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
