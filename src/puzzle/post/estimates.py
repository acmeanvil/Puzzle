"""Error estimates (ADR 0066 phase 2): how far a solved field is from the exact one,
per element and over the model, from the stored displacement or temperature, the mesh
and the materials, so the same estimate works for every solver.

Zienkiewicz–Zhu: each element's stress (or heat flux) from the solved field's
gradient, recovered to a smoother nodal field by averaging the elements around each
node (separately per material, so an interface's jump is kept), and the difference
measured in the energy norm:

    e_K² = ∫_K (σ* − σ_h)ᵀ C (σ* − σ_h) dV   (C the compliance; heat: k⁻¹ and the flux)

with the model's relative error η = √(Σ e_K² / (Σ e_K² + Σ U_K²)), U_K² = ∫_K σ_hᵀ C σ_h.
Zienkiewicz and Zhu's refinement ratio, ξ_K = e_K / (η̄ √((ΣU² + Σe²) / N)) for a target
η̄, is above 1 where an element is too big (phase 3 sizes the next mesh from it).

Gradient jumps: the normal stress (or flux) jumping across each face between two
elements, η_K² = ½ Σ_F h_F A_F |[σ n]|² / E (heat: / k), in the same units. The
boundary's faces are left out: their loads are not known here.

Linear and quadratic tetrahedra; other elements are left out with a note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import numpy as np

from puzzle.errors import PuzzleError

# The 4-point rule on the reference tetrahedron (degree 2), weights summing to 1/6.
_A, _B = 0.5854101966249685, 0.1381966011250105
GAUSS4 = np.array([[_B, _B, _B], [_A, _B, _B], [_B, _A, _B], [_B, _B, _A]])
W4 = np.full(4, 1 / 24)

# Gmsh's tet10 edge nodes: 4 (0,1), 5 (1,2), 6 (2,0), 7 (3,0), 8 (3,2), 9 (3,1).
EDGES10 = [(0, 1), (1, 2), (2, 0), (3, 0), (3, 2), (3, 1)]
CORNERS = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
NODES10 = np.vstack([CORNERS, [(CORNERS[a] + CORNERS[b]) / 2 for a, b in EDGES10]])
FACES = [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)]  # corner triples


def _composite():
    """The 4-point rule on each of the reference tetrahedron's eight sub-tetrahedra
    (32 points): accurate enough for a quadratic element's error squared."""
    m = {(a, b): (CORNERS[a] + CORNERS[b]) / 2 for a in range(4) for b in range(a + 1, 4)}
    c = CORNERS
    subs = [
        [c[0], m[0, 1], m[0, 2], m[0, 3]], [m[0, 1], c[1], m[1, 2], m[1, 3]],
        [m[0, 2], m[1, 2], c[2], m[2, 3]], [m[0, 3], m[1, 3], m[2, 3], c[3]],
        [m[0, 1], m[0, 2], m[0, 3], m[1, 3]], [m[0, 1], m[0, 2], m[1, 2], m[1, 3]],
        [m[0, 2], m[0, 3], m[1, 3], m[2, 3]], [m[0, 2], m[1, 2], m[1, 3], m[2, 3]],
    ]  # fmt: skip
    pts, wts = [], []
    for s in subs:
        s = np.asarray(s)
        vol = abs(np.linalg.det(s[1:] - s[0])) / 6
        for g, w in zip(GAUSS4, W4, strict=True):
            pts.append(s[0] + (s[1:] - s[0]).T @ g)
            wts.append(w * vol * 6)
    return np.asarray(pts), np.asarray(wts)


GAUSS32, W32 = _composite()


def shape(element: str, xi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shape functions (q, n) and their reference derivatives (q, n, 3) at points xi."""
    xi = np.atleast_2d(xi)
    lam = np.column_stack([1 - xi.sum(axis=1), xi])  # barycentric L0..L3
    dlam = np.array([[-1.0, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]])  # dL_i/dxi
    if element == "tet4":
        return lam, np.broadcast_to(dlam, (len(xi), 4, 3)).copy()
    if element == "tet10":
        n = np.empty((len(xi), 10))
        dn = np.empty((len(xi), 10, 3))
        for i in range(4):
            n[:, i] = lam[:, i] * (2 * lam[:, i] - 1)
            dn[:, i] = (4 * lam[:, i] - 1)[:, None] * dlam[i]
        for k, (a, b) in enumerate(EDGES10, start=4):
            n[:, k] = 4 * lam[:, a] * lam[:, b]
            dn[:, k] = 4 * (lam[:, a][:, None] * dlam[b] + lam[:, b][:, None] * dlam[a])
        return n, dn
    raise PuzzleError(f"no shape functions for {element}", code="unsupported")


def _gradients(x: np.ndarray, dn: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Physical shape-function gradients (e, q, n, 3) and |det J| (e, q) for element
    coordinates x (e, n, 3) and reference derivatives dn (q, n, 3)."""
    jac = np.einsum("eni,qnj->eqij", x, dn)
    det = np.linalg.det(jac)
    inv = np.linalg.inv(jac)
    return np.einsum("qnk,eqkj->eqnj", dn, inv), np.abs(det)


def _flux(physics: str, grad: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """Stress in Voigt order xx yy zz xy yz zx (structural), heat flux −k ∇T, or any
    field's gradient (each component's, flattened), from gradients (..., c, 3) and each
    element's (E, ν), (k,) or (1,) broadcast to them."""
    if physics == "field":
        return grad.reshape(*grad.shape[:-2], -1)
    if physics == "heat":
        return -mat[..., :1] * grad[..., 0, :]
    e, nu = mat[..., 0], mat[..., 1]
    lam = e * nu / ((1 + nu) * (1 - 2 * nu))
    mu = e / (2 * (1 + nu))
    eps = 0.5 * (grad + np.swapaxes(grad, -1, -2))
    tr = eps[..., 0, 0] + eps[..., 1, 1] + eps[..., 2, 2]
    s = [2 * mu * eps[..., i, i] + lam * tr for i in range(3)]
    s += [2 * mu * eps[..., 0, 1], 2 * mu * eps[..., 1, 2], 2 * mu * eps[..., 2, 0]]
    return np.stack(s, axis=-1)


def _energy(physics: str, s: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """sᵀ C s: the compliance's quadratic form, per point."""
    if physics == "field":
        return (s**2).sum(axis=-1)
    if physics == "heat":
        return (s**2).sum(axis=-1) / mat[..., 0]
    e, nu = mat[..., 0], mat[..., 1]
    n, sh = s[..., :3], s[..., 3:]
    tr = n.sum(axis=-1)
    return ((1 + nu) * (n**2).sum(axis=-1) - nu * tr**2) / e + 2 * (1 + nu) / e * (sh**2).sum(
        axis=-1
    )


@dataclass
class Estimate:
    """An error estimate per element: the ZZ error and energy (√J for a structure,
    √(W·m·K⁻¹)... for heat: the energy norm's units), volume, size, and the jump
    indicator; `relative` is the model's η."""

    physics: str
    element_ids: np.ndarray
    error: np.ndarray
    energy: np.ndarray
    volume: np.ndarray
    size: np.ndarray
    jump: np.ndarray
    order: int
    notes: list[str] = field(default_factory=list)

    @property
    def relative(self) -> float:
        e2, u2 = float((self.error**2).sum()), float((self.energy**2).sum())
        return float(np.sqrt(e2 / (e2 + u2))) if e2 + u2 > 0 else 0.0

    def ratio(self, target: float) -> np.ndarray:
        """Zienkiewicz–Zhu's refinement ratio per element for a target η (0.02 = 2 %):
        above 1, the element is too big."""
        total = float((self.error**2).sum() + (self.energy**2).sum())
        allowed = target * np.sqrt(total / max(len(self.error), 1))
        return self.error / allowed if allowed > 0 else np.zeros_like(self.error)

    def sizes(self, target: float, smallest: float = 0.0, largest: float = np.inf):
        """Each element's new size for the target: h ξ^(−1/p), p the element order, so
        each element's error comes out equal and the total at the target."""
        xi = np.maximum(self.ratio(target), 1e-6)
        return np.clip(self.size * xi ** (-1.0 / self.order), smallest, largest)

    def fields(self, target: float = 0.02) -> dict:
        """The estimate as stored fields: per element error, refinement ratio and jump
        indicator; the model's relative error as a global field."""
        from puzzle.results.model import Field

        ids = self.element_ids

        def one(name, values):
            return Field(name, "element", [""], ids, np.asarray(values, float)[:, None])

        return {
            "error_estimate": one("error_estimate", self.error),
            "error_ratio": one("error_ratio", self.ratio(target)),
            "error_jump": one("error_jump", self.jump),
            "error_relative": Field(
                "error_relative", "global", [""], np.array([0]), np.array([[self.relative]])
            ),  # fmt: skip
        }


def estimate(mesh, values_at, materials, physics: str = "structural") -> Estimate:
    """The ZZ estimate and jump indicator for a solved field.

    `values_at(node_ids)` gives the nodal field ((n, 3) displacement, (n, 1)
    temperature, or any (n, c) field); `materials` per element block: a function
    (block) -> (E, ν) or (k,) per element, or one tuple for all (`field`: (1,), its
    estimate then relative only: a gradient's smoothness, not an energy)."""
    if physics not in ("structural", "heat", "field"):
        raise PuzzleError(f"no error estimate for {physics}", code="unsupported")
    notes: list[str] = []
    blocks = [b for b in mesh.blocks if b.dim == 3]
    used = [b for b in blocks if b.element in ("tet4", "tet10")]
    skipped = sorted({b.element for b in blocks} - {"tet4", "tet10"})
    if skipped:
        notes.append(f"{', '.join(skipped)} elements are left out of the estimate")
    if not used:
        raise PuzzleError("the error estimate needs tetrahedra", code="unsupported")
    order = 2 if any(b.element == "tet10" for b in used) else 1
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    per: list[dict] = []
    for b in used:
        conn = np.vectorize(pos.__getitem__)(b.connectivity)
        x = mesh.coords[conn]
        u = values_at(b.connectivity.reshape(-1)).reshape(*b.connectivity.shape, -1)
        mat = np.asarray(materials(b) if callable(materials) else materials, float)
        if mat.ndim == 1:
            mat = np.tile(mat, (len(conn), 1))
        nodes = CORNERS if b.element == "tet4" else NODES10
        _, dn_nodes = shape(b.element, nodes)
        g_nodes, _ = _gradients(x, dn_nodes)
        grad_nodes = np.einsum("enc,eqnj->eqcj", u, g_nodes)  # (e, nodes, c, 3)
        flux_nodes = _flux(physics, grad_nodes, mat[:, None, :])
        pts, wts = (GAUSS4, W4) if b.element == "tet4" else (GAUSS32, W32)
        n_q, dn_q = shape(b.element, pts)
        g_q, det = _gradients(x, dn_q)
        flux_q = _flux(physics, np.einsum("enc,eqnj->eqcj", u, g_q), mat[:, None, :])
        per.append({"block": b, "conn": conn, "x": x, "u": u, "mat": mat, "n_q": n_q,
                    "det": det, "w": wts, "flux_nodes": flux_nodes, "flux_q": flux_q})  # fmt: skip
    # recovery: each node's average over the elements of one material around it
    allmats, which = np.unique(np.vstack([d["mat"] for d in per]), axis=0,
                               return_inverse=True)  # fmt: skip
    which = which.reshape(-1)
    width = per[0]["flux_nodes"].shape[-1]
    total = np.zeros((len(allmats), len(mesh.node_ids), width))
    weight = np.zeros((len(allmats), len(mesh.node_ids)))
    start = 0
    for d in per:
        mi = which[start : start + len(d["conn"])]
        start += len(d["conn"])
        vol = (d["det"] * d["w"]).sum(axis=1)
        d["mi"], d["vol"] = mi, vol
        for k in range(d["conn"].shape[1]):
            np.add.at(total, (mi, d["conn"][:, k]), d["flux_nodes"][:, k] * vol[:, None])
            np.add.at(weight, (mi, d["conn"][:, k]), vol)
    recovered = total / np.maximum(weight, 1e-300)[..., None]
    ids, err, eng, vols, size = [], [], [], [], []
    for d in per:
        star_nodes = recovered[d["mi"][:, None], d["conn"]]  # (e, n, c)
        star_q = np.einsum("qn,enc->eqc", d["n_q"], star_nodes)
        dw = d["det"] * d["w"]
        diff = _energy(physics, star_q - d["flux_q"], d["mat"][:, None, :])
        energy = _energy(physics, d["flux_q"], d["mat"][:, None, :])
        err.append(np.sqrt(np.maximum((diff * dw).sum(axis=1), 0)))
        eng.append(np.sqrt(np.maximum((energy * dw).sum(axis=1), 0)))
        vols.append(d["vol"])
        corners = d["x"][:, :4]
        edges = [np.linalg.norm(corners[:, a] - corners[:, b], axis=1)
                 for a in range(4) for b in range(a + 1, 4)]  # fmt: skip
        size.append(np.max(edges, axis=0))
        ids.append(d["block"].ids)
    jump = _jumps(per, physics)
    return Estimate(physics, np.concatenate(ids), np.concatenate(err), np.concatenate(eng),
                    np.concatenate(vols), np.concatenate(size), jump, order, notes)  # fmt: skip


def _jumps(per: list[dict], physics: str) -> np.ndarray:
    """½ Σ_F h_F A_F |[flux · n]|² / (E or k) per element, over faces shared by two.

    Each element's flux is linear (constant on tet4), so its value at a face's centroid
    is the mean of the face's corner values."""
    corners = np.concatenate([d["conn"][:, :4] for d in per])
    x = np.concatenate([d["x"][:, :4] for d in per])
    flux = np.concatenate([d["flux_nodes"][:, :4] for d in per])
    scale = np.concatenate([d["mat"][:, 0] for d in per])
    n_el = len(corners)
    local = np.array(FACES)  # (4, 3)
    tri_nodes = corners[:, local]  # (E, 4, 3)
    keys = np.sort(tri_nodes, axis=2).reshape(-1, 3)
    elem = np.repeat(np.arange(n_el), 4)
    face = np.tile(np.arange(4), n_el)
    order = np.lexsort(keys.T[::-1])
    k = keys[order]
    same = (k[1:] == k[:-1]).all(axis=1)
    a, b = order[:-1][same], order[1:][same]  # the two sides of each interior face
    ea, fa, eb, fb = elem[a], face[a], elem[b], face[b]
    tri = x[ea[:, None], local[fa]]  # (F, 3, 3)
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = np.linalg.norm(cross, axis=1) / 2
    normal = cross / np.maximum(2 * area, 1e-300)[:, None]
    h = np.max([np.linalg.norm(tri[:, i] - tri[:, j], axis=1)
                for i, j in ((0, 1), (1, 2), (2, 0))], axis=0)  # fmt: skip
    fa_val = flux[ea[:, None], local[fa]].mean(axis=1)
    fb_val = flux[eb[:, None], local[fb]].mean(axis=1)
    j2 = (_traction(physics, fa_val - fb_val, normal) ** 2).sum(axis=1)
    share = 0.25 * h * area * j2 * (1 / scale[ea] + 1 / scale[eb])  # ½ Σ, half a side
    out = np.zeros(n_el)
    np.add.at(out, ea, share)
    np.add.at(out, eb, share)
    return np.sqrt(out)


def _traction(physics: str, s: np.ndarray, n: np.ndarray) -> np.ndarray:
    """σ n (or q · n, or each component's ∇f · n) per face: s (F, 6, 3 or 3c), n (F, 3)."""
    if physics == "field":
        return np.einsum("fcj,fj->fc", s.reshape(len(s), -1, 3), n)
    if physics == "heat":
        return (s * n).sum(axis=1)[:, None]
    xx, yy, zz, xy, yz, zx = s.T
    nx, ny, nz = n.T
    return np.column_stack([xx * nx + xy * ny + zx * nz, xy * nx + yy * ny + yz * nz,
                            zx * nx + yz * ny + zz * nz])  # fmt: skip


PHYSICS = {"displacement": "structural", "temperature": "heat"}


def of_run(project, store, run_id: str, *, field_name: str | None = None,
           step: int = -1) -> Estimate:  # fmt: skip
    """The estimate for a stored run's step: its displacement (structural, each
    element's E and ν from the component's assignments), its temperature (heat, k), or
    `field_name`'s gradient jumps on any nodal field (relative only)."""
    from puzzle.model.schema import Component, Material, Study

    record = next((r for r in store.runs() if r.run_id == run_id), None)
    if record is None:
        raise PuzzleError(f"no run '{run_id}'", code="not_found")
    results = store.read_results(run_id)
    mesh = store.read_mesh(record.mesh_id)
    st = results.steps[step]
    name = field_name or next((n for n in PHYSICS if n in st.fields), None)
    if name is None or name not in st.fields:
        raise PuzzleError("the run has no displacement or temperature to estimate from; "
                          "name a field", code="invalid")  # fmt: skip
    f = st.fields[name]
    if f.location != "node":
        raise PuzzleError(f"'{name}' is per {f.location}: an estimate needs nodal values",
                          code="unsupported")  # fmt: skip
    physics = PHYSICS.get(name, "field") if field_name is None or name in PHYSICS else "field"
    if physics == "field":
        return estimate(mesh, f.at, (1.0,), "field")
    study = project.get(UUID(record.study_id), Study)
    comp = project.get(study.component, Component)
    keys = ("youngs_modulus", "poissons_ratio") if physics == "structural" else (
        "thermal_conductivity",)  # fmt: skip
    by_entity: dict[tuple, tuple] = {}
    first = None
    for a in comp.materials:
        m = project.get(a.material, Material)
        missing = [k for k in keys if k not in m.properties]
        if missing:
            raise PuzzleError(f"'{m.name}' has no {', '.join(missing)}",
                              code="incomplete_material")  # fmt: skip
        vals = tuple(float(m.properties[k].to_si()) for k in keys)
        first = first or vals
        group = mesh.groups.get(a.selection)
        for ent in group.entities if group is not None else []:
            by_entity[tuple(ent)] = vals
    if first is None:
        raise PuzzleError(f"'{comp.name}' has no material", code="incomplete_material")

    def materials(block):
        return np.tile(by_entity.get(tuple(block.entity), first), (len(block.ids), 1))

    return estimate(mesh, f.at, materials, physics)


def hot_spots(mesh, est: Estimate, count: int = 5) -> list[dict]:
    """The `count` elements with the largest error: id, error, centre (m), and the
    named selections (not a part's bodies) whose nodes it touches, "at the fillet"."""
    pos = {int(n): i for i, n in enumerate(mesh.node_ids)}
    by_id = {}
    for b in mesh.blocks:
        if b.dim == 3:
            for eid, row in zip(b.ids, b.connectivity, strict=True):
                by_id[int(eid)] = row
    named = {name: set(map(int, g.node_ids)) for name, g in mesh.groups.items()
             if g.dim < 3}  # fmt: skip
    out = []
    for k in np.argsort(est.error)[::-1][:count]:
        eid = int(est.element_ids[k])
        nodes = by_id.get(eid)
        if nodes is None:
            continue
        centre = mesh.coords[[pos[int(n)] for n in nodes[:4]]].mean(axis=0)
        touch = sorted(name for name, ns in named.items() if ns.intersection(map(int, nodes)))
        out.append({"element": eid, "error": float(est.error[k]),
                    "centre": [float(c) for c in centre], "selections": touch})  # fmt: skip
    return out


def summary(mesh, est: Estimate, target: float = 0.02) -> dict:
    """The estimate as plain data (CLI --json, MCP): the relative error, the elements
    above the target's ratio, the hot spots."""
    ratio = est.ratio(target)
    return {"physics": est.physics, "relative_error": est.relative, "target": target,
            "elements": int(len(est.error)), "too_big": int((ratio > 1).sum()),
            "order": est.order, "hot_spots": hot_spots(mesh, est), "notes": est.notes}  # fmt: skip


def words(mesh, est: Estimate, target: float = 0.02) -> str:
    """A sentence: "error 4.2 % (energy norm), largest at B/Fillet: refine there"."""
    spots = hot_spots(mesh, est, 3)
    where = next((s["selections"][0] for s in spots if s["selections"]), "")
    at = f", largest at {where}" if where else ""
    if est.physics == "field":
        return f"the gradient jumps are largest{at.removeprefix(',')}"
    pct = est.relative * 100
    verdict = ": refine there" if est.relative > target else f", within {target * 100:g} %"
    return f"error {pct:.2g} % (energy norm){at}{verdict}"
