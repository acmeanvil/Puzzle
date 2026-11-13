"""Aluminium member strengths by the Aluminum Design Manual 2015, Part I, the
Specification for Aluminum Structures (ADR 0079 step 5), in SI, from its own equations
(read from its pages), for building-type structures:

- buckling constants (B.4): Table B.4.1 for O, H, T1-T4 tempers, Table B.4.2 for T5-T9
  (the temper read from the material's name), C_t found where the inelastic and elastic
  curves meet; k1, k2 from Table B.4.3;
- elements (B.5.4, B.5.5): flat ones supported on one edge (B.5.4.1) or both (B.5.4.2),
  round ones (B.5.4.5) in uniform compression; flat ones in flexure supported on both
  edges (B.5.5.1, m = 0.65 for a web symmetric about the neutral axis) or on the
  tension edge (B.5.5.2), pipes (B.5.5.4); elastic stresses by Table B.5.1;
- tension D.2 (k_t = 1), compression E.2 (flexural, E.2.1; torsional, E.2.2a), E.3.1's
  weighted average local buckling and E.4's interaction; flexure F.2 (yielding,
  rupture), F.3.1 (an I or a box: flanges in uniform compression, webs in flexure) or
  F.3.3 (the limiting element), F.4 (C_b, r_ye by F.4-4, closed shapes F.4-6, bars
  F.4-7) and F.4.3; shear G.2, G.3, G.4, G.5; combined H.1-1, linear.

Flat widths follow the Manual's examples: an I's flange (b_f − t_w)/2 and web d − 2t_f
(fillets left out); a tube's wall the outside less 2t, less twice the inside corner
radius where the table gives the AISC convention (b = outside − 3t). Welded members
(heat-affected zones) and torsion (H.2) are not built (`NotCovered`).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from puzzle.frames.aisc360 import NotCovered, Props, Strength

KAPPA = 6.895e6  # Pa: Table B.4.1's κ = 1 ksi


@dataclass(frozen=True)
class Aluminium:
    E: float
    G: float
    Fty: float
    Ftu: float
    Fcy: float
    Fsy: float
    Fsu: float
    artificially_aged: bool  # T5-T9: Table B.4.2

    @property
    def Fy(self) -> float:  # what the shared checks call the yield
        return self.Fty


def material(m) -> Aluminium:
    props = {k: q.to_si() for k, q in m.properties.items()}
    missing = [
        k for k in ("youngs_modulus", "yield_strength", "tensile_strength") if k not in props
    ]
    if missing:
        raise NotCovered(f"material '{m.name}' has no {', '.join(missing)}")
    e = props["youngs_modulus"]
    fty, ftu = props["yield_strength"], props["tensile_strength"]
    temper = re.search(r"-(O|H\d+|T\d+)", m.name or "")
    aged = bool(temper and temper.group(1).startswith("T") and temper.group(1)[1] in "56789")
    return Aluminium(e, e / (2 * (1 + props.get("poissons_ratio", 0.33))), fty, ftu,
                     props.get("compressive_yield_strength", fty), 0.6 * fty, 0.6 * ftu,
                     aged)  # fmt: skip


@dataclass(frozen=True)
class Constants:
    Bc: float
    Dc: float
    Cc: float
    Bp: float
    Dp: float
    Cp: float
    Bt: float
    Dt: float
    Ct: float
    Bbr: float
    Dbr: float
    Cbr: float
    Btb: float
    Dtb: float
    Ctb: float
    Bs: float
    Ds: float
    Cs: float
    k1: float
    k2: float


def constants(a: Aluminium) -> Constants:
    """Tables B.4.1 / B.4.2 and B.4.3."""
    e, fcy, fsy, k = a.E, a.Fcy, a.Fsy, KAPPA
    if a.artificially_aged:  # Table B.4.2
        bc = fcy * (1 + (fcy / (2250 * k)) ** 0.5)
        dc = bc / 10 * (bc / e) ** 0.5
        cc = 0.41 * bc / dc
        bp = fcy * (1 + (fcy / (1500 * k)) ** (1 / 3))
        dp = bp / 10 * (bp / e) ** 0.5
        cp = 0.41 * bp / dp
        bt = fcy * (1 + (fcy / (50000 * k)) ** 0.2)
        dt = bt / 4.5 * (bt / e) ** (1 / 3)
        btb = 1.5 * fcy * (1 + (fcy / (50000 * k)) ** 0.2)
        bs = fsy * (1 + (fsy / (800 * k)) ** (1 / 3))
        ds = bs / 10 * (bs / e) ** 0.5
        cs = 0.41 * bs / ds
        k1, k2 = 0.35, 2.27
    else:  # Table B.4.1
        bc = fcy * (1 + (fcy / (1000 * k)) ** 0.5)
        dc = bc / 20 * (6 * bc / e) ** 0.5
        cc = 2 * bc / (3 * dc)
        bp = fcy * (1 + (fcy / (440 * k)) ** (1 / 3))
        dp = bp / 20 * (6 * bp / e) ** 0.5
        cp = 2 * bp / (3 * dp)
        bt = fcy * (1 + (fcy / (6500 * k)) ** 0.2)
        dt = bt / 3.7 * (bt / e) ** (1 / 3)
        btb = 1.5 * fcy * (1 + (fcy / (6500 * k)) ** 0.2)
        bs = fsy * (1 + (fsy / (240 * k)) ** (1 / 3))
        ds = bs / 20 * (6 * bs / e) ** 0.5
        cs = 2 * bs / (3 * ds)
        k1, k2 = 0.50, 2.04
    bbr = 1.3 * fcy * (1 + (fcy / (340 * k)) ** (1 / 3))
    dbr = bbr / 20 * (6 * bbr / e) ** 0.5
    cbr = 2 * bbr / (3 * dbr)
    dtb = btb / 2.7 * (btb / e) ** (1 / 3)
    ctb = ((btb - bt) / (dtb - dt)) ** 2
    ct = _ct(bt, dt, e)
    return Constants(bc, dc, cc, bp, dp, cp, bt, dt, ct, bbr, dbr, cbr, btb, dtb, ctb, bs, ds,
                     cs, k1, k2)  # fmt: skip


def _round_elastic(rt: float, e: float) -> float:
    return math.pi**2 * e / (16 * rt * (1 + math.sqrt(rt) / 35) ** 2)


def _ct(bt: float, dt: float, e: float) -> float:
    """C_t: R_b/t where B_t − D_t √(R_b/t) meets the elastic curve (Table B.4.1 note 2)."""
    f = lambda x: (bt - dt * math.sqrt(x)) - _round_elastic(x, e)  # noqa: E731
    # the elastic curve is the higher at small R_b/t; C_t is where the inelastic line
    # first rises above it (further out the line itself turns negative)
    x = 0.01
    while f(x) <= 0:
        x *= 1.05
        if x > 1e6:
            raise ValueError("the round-element curves don't meet")
    lo, hi = x / 1.05, x
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if f(mid) > 0 else (mid, hi)
    return (lo + hi) / 2
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return lo


# --- elements ---------------------------------------------------------------------------


def one_edge(bt: float, a: Aluminium, c: Constants) -> float:
    """B.5.4.1: uniform compression, supported on one edge (post-buckling: beams and
    columns buckling about an axis of symmetry)."""
    l1 = (c.Bp - a.Fcy) / (5.0 * c.Dp)
    l2 = c.k1 * c.Bp / (5.0 * c.Dp)
    if bt <= l1:
        return a.Fcy
    if bt < l2:
        return c.Bp - 5.0 * c.Dp * bt
    return c.k2 * math.sqrt(c.Bp * a.E) / (5.0 * bt)


def both_edges(bt: float, a: Aluminium, c: Constants) -> float:
    """B.5.4.2: uniform compression, supported on both edges."""
    l1 = (c.Bp - a.Fcy) / (1.6 * c.Dp)
    l2 = c.k1 * c.Bp / (1.6 * c.Dp)
    if bt <= l1:
        return a.Fcy
    if bt < l2:
        return c.Bp - 1.6 * c.Dp * bt
    return c.k2 * math.sqrt(c.Bp * a.E) / (1.6 * bt)


def round_compression(rt: float, a: Aluminium, c: Constants) -> float:
    """B.5.4.5."""
    l1 = ((c.Bt - a.Fcy) / c.Dt) ** 2
    if rt <= l1:
        return a.Fcy
    if rt < c.Ct:
        return c.Bt - c.Dt * math.sqrt(rt)
    return _round_elastic(rt, a.E)


def web_flexure(bt: float, a: Aluminium, c: Constants, m: float = 0.65) -> float:
    """B.5.5.1: flexural compression, supported on both edges (k1, k2 for flexure)."""
    k1, k2 = 0.50, 2.04
    l1 = (c.Bbr - 1.5 * a.Fcy) / (m * c.Dbr)
    l2 = k1 * c.Bbr / (m * c.Dbr)
    if bt <= l1:
        return 1.5 * a.Fcy
    if bt < l2:
        return c.Bbr - m * c.Dbr * bt
    return k2 * math.sqrt(c.Bbr * a.E) / (m * bt)


def free_edge_flexure(bt: float, a: Aluminium, c: Constants) -> float:
    """B.5.5.2: flexural compression, supported on the tension edge, compression free."""
    l1 = (c.Bbr - 1.5 * a.Fcy) / (3.5 * c.Dbr)
    l2 = c.Cbr / 3.5
    if bt <= l1:
        return 1.5 * a.Fcy
    if bt < l2:
        return c.Bbr - 3.5 * c.Dbr * bt
    return math.pi**2 * a.E / (3.5 * bt) ** 2


def pipe_flexure(rt: float, a: Aluminium, c: Constants) -> float:
    """B.5.5.4."""
    if rt <= c.Ctb:
        return c.Btb - c.Dtb * math.sqrt(rt)
    if rt < c.Ct:
        return c.Bt - c.Dt * math.sqrt(rt)
    return _round_elastic(rt, a.E)


# --- sections as elements ---------------------------------------------------------------


def _walls(p: Props) -> tuple[float, float]:
    """A tube's flat widths (along x: the flanges, across: the webs)."""
    inside = p.extra.get("corner", 0.5 * p.t if p.designation.startswith("HSS") else 0.0)
    return p.bf - 2 * p.t - 2 * inside, p.d - 2 * p.t - 2 * inside


def _rb(p: Props) -> float:
    return (p.d - p.t) / 2 / p.t  # R_b/t, R_b at mid-thickness (B.5.2)


# --- strengths --------------------------------------------------------------------------


def tension(p: Props, a: Aluminium, net_ratio: float = 1.0) -> list[Strength]:
    """D.2 for unwelded members, k_t = 1."""
    return [Strength(a.Fty * p.A, 0.90, 1.65, "tensile yielding", "D.2(a)"),
            Strength(a.Ftu * net_ratio * p.A, 0.75, 1.95, "tensile rupture", "D.2(b)",
                     (f"A_e = {net_ratio:g} A_g, k_t 1",))]  # fmt: skip


def member_buckling(lam: float, a: Aluminium, c: Constants) -> float:
    """E.2's F_c."""
    l1 = (c.Bc - a.Fcy) / c.Dc
    if lam <= l1:
        return a.Fcy
    if lam < c.Cc:
        return (c.Bc - c.Dc * lam) * (0.85 + 0.15 * (c.Cc - lam) / (c.Cc - l1))
    return 0.85 * math.pi**2 * a.E / lam**2


def _local(p: Props, a: Aluminium, c: Constants) -> tuple[float, float, str]:
    """(local buckling P_nc by E.3.1, the least elastic local buckling stress F_e of
    Table B.5.1, the elements' words)."""
    if p.kind == "I":
        bf, bw = (p.bf - p.tw) / 2 / p.tf, (p.d - 2 * p.tf) / p.tw
        af, aw = 2 * (p.bf - p.tw) * p.tf, (p.d - 2 * p.tf) * p.tw
        fcf, fcw = one_edge(bf, a, c), both_edges(bw, a, c)
        pnc = fcf * af + fcw * aw + a.Fcy * (p.A - af - aw)
        fe = min(math.pi**2 * a.E / (5.0 * bf) ** 2, math.pi**2 * a.E / (1.6 * bw) ** 2)
        return pnc, fe, f"flanges b/t {bf:.3g}, web b/t {bw:.3g}"
    if p.kind == "RHS":
        b1, b2 = _walls(p)
        pnc, fe, area = 0.0, math.inf, 0.0
        for b in (b1, b2):
            fc = both_edges(b / p.t, a, c)
            pnc += 2 * fc * b * p.t
            area += 2 * b * p.t
            fe = min(fe, math.pi**2 * a.E / (1.6 * b / p.t) ** 2)
        return pnc + a.Fcy * (p.A - area), fe, f"walls b/t {b1 / p.t:.3g}, {b2 / p.t:.3g}"
    if p.kind == "CHS":
        rt = _rb(p)
        return round_compression(rt, a, c) * p.A, _round_elastic(rt, a.E), f"R_b/t {rt:.3g}"
    return a.Fcy * p.A, math.inf, "solid: no local buckling"


def compression(p: Props, a: Aluminium, lcx: float, lcy: float, lcz: float) -> Strength:
    """E.1: the least of member buckling (E.2), local buckling (E.3.1) and their
    interaction (E.4)."""
    c = constants(a)
    lams = [("flexural buckling about x", lcx / p.rx if lcx > 0 else 0.0),
            ("flexural buckling about y", lcy / p.ry if lcy > 0 else 0.0)]  # fmt: skip
    if p.kind == "I" and lcz > 0:  # E.2.2a
        fe = (math.pi**2 * a.E * p.Cw / lcz**2 + a.G * p.J) / (p.Ix + p.Iy)
        lams.append(("torsional buckling", math.pi * math.sqrt(a.E / fe)))
    limit, lam = max(lams, key=lambda x: x[1])
    fc = member_buckling(lam, a, c)
    best = Strength(fc * p.A, 0.90, 1.65, f"member {limit}", "E.2", (f"λ {lam:.4g}",))
    pnc, fe, words = _local(p, a, c)
    if pnc < best.nominal:
        best = Strength(pnc, 0.90, 1.65, "local buckling", "E.3.1", (words,))
    if fe < fc and lam > 0:  # E.4
        cap = (0.85 * math.pi**2 * a.E / lam**2) ** (1 / 3) * fe ** (2 / 3) * p.A
        if cap < best.nominal:
            best = Strength(cap, 0.90, 1.65, "member and local buckling", "E.4", (words,))
    return best


def _yield_moment(z: float, s: float, a: Aluminium) -> float:
    return min(z * a.Fcy, 1.5 * s * a.Fty, 1.5 * s * a.Fcy)  # F.2, wrought


def flexure_x(
    p: Props, a: Aluminium, lb: float, cb: float, rye: float | None = None, method: str = "LRFD"
) -> Strength:
    """F.1: yielding and rupture (F.2), local buckling (F.3), lateral-torsional
    buckling (F.4, F.4.3)."""
    c = constants(a)
    mnp = _yield_moment(p.Zx, p.Sx, a)
    out = [Strength(mnp, 0.90, 1.65, "yielding", "F.2"),
           Strength(p.Zx * a.Ftu, 0.75, 1.95, "rupture", "F.2-1")]  # fmt: skip
    if p.kind == "I":
        bf, bw = (p.bf - p.tw) / 2 / p.tf, (p.d - 2 * p.tf) / p.tw
        fc, fb = one_edge(bf, a, c), web_flexure(bw, a, c)
        iw = p.tw * (p.d - 2 * p.tf) ** 3 / 12
        mnlb = fc * (p.Ix - iw) / (p.d / 2 - p.tf / 2) + fb * iw / (p.d / 2 - p.tf)
        out.append(Strength(mnlb, 0.90, 1.65, "local buckling", "F.3.1"))
    elif p.kind == "RHS":
        b1, b2 = _walls(p)
        fc, fb = both_edges(b1 / p.t, a, c), web_flexure(b2 / p.t, a, c)
        h = p.d - 2 * p.t
        iw = 2 * p.t * h**3 / 12
        mnlb = fc * (p.Ix - iw) / (p.d / 2 - p.t / 2) + fb * iw / (p.d / 2 - p.t)
        out.append(Strength(mnlb, 0.90, 1.65, "local buckling", "F.3.1"))
    elif p.kind == "CHS":
        out.append(Strength(pipe_flexure(_rb(p), a, c) * p.Sx, 0.90, 1.65, "local buckling",
                            "F.3.3, B.5.5.4"))  # fmt: skip
    ltb = _ltb(p, a, c, lb, cb, mnp, rye)
    if ltb is not None:
        out.append(ltb)
    return min(out, key=lambda s: s.available(method))


def _ltb(p, a, c, lb, cb, mnp, rye) -> Strength | None:
    if lb <= 0 or p.kind in ("CHS", "round"):
        return None
    if p.kind == "I":  # F.4.2.1, F.4-4 (loads at the neutral axis or at brace points)
        r = rye or math.sqrt(math.sqrt(p.Iy) / p.Sx * math.sqrt(p.Cw + 0.038 * p.J * lb**2))
        lam = lb / (r * math.sqrt(cb))
    elif p.kind == "RHS":  # F.4-6
        lam = 2.3 * math.sqrt(lb * p.Sx / (cb * math.sqrt(p.Iy * p.J)))
    else:  # F.4-7, a bar: d across x, t = b_f along it
        lam = 2.3 / p.bf * math.sqrt(p.d * lb / cb)
    if lam < c.Cc:
        m = mnp * (1 - lam / c.Cc) + math.pi**2 * a.E * lam * p.Sx / c.Cc**3
    else:
        m = math.pi**2 * a.E * p.Sx / lam**2
    m = min(m, mnp)
    notes = [f"λ {lam:.4g}, C_c {c.Cc:.4g}, C_b {cb:.3f}"]
    clause = "F.4"
    if p.kind == "I":  # F.4.3: the flange's elastic buckling against the beam's F_b
        fe = math.pi**2 * a.E / (5.0 * (p.bf - p.tw) / 2 / p.tf) ** 2
        if fe < m / p.Sx:
            cap = (math.pi**2 * a.E / lam**2) ** (1 / 3) * fe ** (2 / 3) * p.Sx
            if cap < m:
                m, clause = cap, "F.4.3"
    return Strength(m, 0.90, 1.65, "lateral-torsional buckling", clause, tuple(notes))


def flexure_y(p: Props, a: Aluminium, method: str = "LRFD") -> Strength:
    c = constants(a)
    mnp = _yield_moment(p.Zy, p.Sy, a)
    out = [Strength(mnp, 0.90, 1.65, "yielding", "F.2"),
           Strength(p.Zy * a.Ftu, 0.75, 1.95, "rupture", "F.2-1")]  # fmt: skip
    if p.kind == "I":  # each half flange: tension edge (at the web) supported, tip free
        fb = free_edge_flexure((p.bf - p.tw) / 2 / p.tf, a, c)
        out.append(Strength(fb * p.Sy, 0.90, 1.65, "local buckling", "F.3.3, B.5.5.2"))
    elif p.kind == "RHS":
        b1, b2 = _walls(p)
        fc, fb = both_edges(b2 / p.t, a, c), web_flexure(b1 / p.t, a, c)
        w = p.bf - 2 * p.t
        iw = 2 * p.t * w**3 / 12
        mnlb = fc * (p.Iy - iw) / (p.bf / 2 - p.t / 2) + fb * iw / (p.bf / 2 - p.t)
        out.append(Strength(mnlb, 0.90, 1.65, "local buckling", "F.3.1"))
    elif p.kind == "CHS":
        out.append(Strength(pipe_flexure(_rb(p), a, c) * p.Sy, 0.90, 1.65, "local buckling",
                            "F.3.3, B.5.5.4"))  # fmt: skip
    return min(out, key=lambda s: s.available(method))


def _shear_flat(bt: float, factor: float, a: Aluminium, c: Constants) -> float:
    """G.2 (factor 1.25, both edges) and G.3 (3.0, one edge)."""
    l1 = (c.Bs - a.Fsy) / (factor * c.Ds)
    l2 = c.Cs / factor
    if bt <= l1:
        return a.Fsy
    if bt < l2:
        return c.Bs - factor * c.Ds * bt
    return math.pi**2 * a.E / (factor * bt) ** 2


def _shear(av: float, fs: float, a: Aluminium, clause: str, method: str = "LRFD") -> Strength:
    """The lesser of shear yielding or buckling (G.1-1) and rupture (F_su A_n/k_t)."""
    return min(Strength(fs * av, 0.90, 1.65, "shear", clause),
               Strength(a.Fsu * av, 0.75, 1.95, "shear rupture", clause),
               key=lambda s: s.available(method))  # fmt: skip


def shear_x(p: Props, a: Aluminium, length: float, method: str = "LRFD") -> Strength | None:
    c = constants(a)
    if p.kind == "I":  # G.2: A_v = d t
        fs = _shear_flat((p.d - 2 * p.tf) / p.tw, 1.25, a, c)
        return _shear(p.d * p.tw, fs, a, "G.2", method=method)
    if p.kind == "RHS":
        _b1, b2 = _walls(p)
        return _shear(2 * p.d * p.t, _shear_flat(b2 / p.t, 1.25, a, c), a, "G.2", method=method)
    if p.kind == "CHS":
        return _pipe_shear(p, a, c, length, method)
    if p.kind == "round":  # G.5
        return _shear(p.A, a.Fsy, a, "G.5", method=method)
    return _shear(p.d * p.bf, _shear_flat(p.d / p.bf, 1.25, a, c), a, "G.2", method=method)


def shear_y(p: Props, a: Aluminium, length: float, method: str = "LRFD") -> Strength | None:
    c = constants(a)
    if p.kind == "I":  # G.3: four half flanges, b to the web's mid-thickness
        fs = _shear_flat(p.bf / 2 / p.tf, 3.0, a, c)
        return _shear(2 * p.bf * p.tf, fs, a, "G.3", method=method)
    if p.kind == "RHS":
        b1, _b2 = _walls(p)
        return _shear(2 * p.bf * p.t, _shear_flat(b1 / p.t, 1.25, a, c), a, "G.2", method=method)
    if p.kind == "CHS":
        return _pipe_shear(p, a, c, length, method)
    if p.kind == "round":
        return _shear(p.A, a.Fsy, a, "G.5", method=method)
    return _shear(p.d * p.bf, _shear_flat(p.bf / p.d, 1.25, a, c), a, "G.2", method=method)


def _pipe_shear(
    p: Props, a: Aluminium, c: Constants, length: float, method: str = "LRFD"
) -> Strength:
    """G.4, L_v half the member."""
    rb = (p.d - p.t) / 2
    lv = max(length / 2, p.d)
    lam = 2.9 * (rb / p.t) ** (5 / 8) * (lv / rb) ** 0.25
    l1 = (1.3 * c.Bs - a.Fsy) / (1.63 * c.Ds)
    l2 = c.Cs / 1.25
    if lam <= l1:
        fs = a.Fsy
    elif lam < l2:
        fs = 1.3 * c.Bs - 1.63 * c.Ds * lam
    else:
        fs = 1.3 * math.pi**2 * a.E / (1.25 * lam) ** 2
    av = math.pi * (p.d**2 - (p.d - 2 * p.t) ** 2) / 8
    return _shear(av, fs, a, "G.4", method=method)


def torsion(p: Props, a: Aluminium, length: float) -> None:
    """H.2 is not built: torsion isn't checked on aluminium members."""
    return None


def interaction(pr: float, pc: float, mrx: float, mcx: float, mry: float, mcy: float) -> float:
    """H.1-1: P_r/P_c + M_rx/M_cx + M_ry/M_cy (linear)."""
    return (pr / pc if pc else 0.0) + (mrx / mcx if mcx else 0.0) + (mry / mcy if mcy else 0.0)
