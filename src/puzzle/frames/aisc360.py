"""Member strengths by AISC 360-16 (ADR 0079 step 3), in SI, from the specification's
own equations: tension (D2), compression (E3, E4 for doubly symmetric members, E7's
effective widths), flexure (F2, F3, F6, F7, F8, F11), shear (G2, G4, G5, G6), torsion of
HSS (H3.1) and combined forces (H1-1, H3.2).

Each limit state returns a `Strength`: the nominal strength, its φ (LRFD) and Ω (ASD),
the limit state's name and the clause, so `available(method)` is φRn or Rn/Ω.

Sections (`Props`): doubly symmetric I-shapes (W, M, S, HP, IPE, HE, UB, UC...),
rectangular and round HSS (and Puzzle's box and tube), solid rectangles and rounds.
Channels, tees, angles and double angles are refused (`NotCovered`): their E4
flexural-torsional buckling with a shear-centre offset and F9-F10 are not built yet.
An I-shape's web noncompact in flexure (F4, F5: built-up girders) is refused too; no
rolled shape at Fy ≤ 65 ksi has one.

Axes: x is the strong axis (the section's first axis, ADR 0055's I1), y the weak one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


class NotCovered(ValueError):
    """A section or case these checks don't cover yet, with the reason."""


@dataclass(frozen=True)
class Strength:
    nominal: float  # N, N·m
    phi: float
    omega: float
    limit: str
    clause: str
    notes: tuple[str, ...] = ()

    def available(self, method: str) -> float:
        return self.phi * self.nominal if method == "LRFD" else self.nominal / self.omega


@dataclass(frozen=True)
class Steel:
    E: float  # Pa
    G: float
    Fy: float
    Fu: float


@dataclass
class Props:
    """What the checks need of a section, SI. `kind`: I, RHS, CHS, bar (a solid
    rectangle, b along x and d across it) or round (a solid circle)."""

    kind: str
    A: float
    Ix: float
    Iy: float
    Zx: float
    Zy: float
    Sx: float
    Sy: float
    J: float
    Cw: float = 0.0
    d: float = 0.0  # overall depth (across x); RHS: H; CHS and round: D
    bf: float = 0.0  # flange width (along x); RHS: B
    tf: float = 0.0
    tw: float = 0.0
    h: float = 0.0  # an I's web clear of fillets (h/tw); RHS: H - 3t
    t: float = 0.0  # HSS design wall
    ho: float = 0.0
    designation: str = ""
    extra: dict = field(default_factory=dict)  # tabulated rx, ry where the table has them

    @property
    def rx(self) -> float:
        return self.extra.get("rx") or math.sqrt(self.Ix / self.A)

    @property
    def ry(self) -> float:
        return self.extra.get("ry") or math.sqrt(self.Iy / self.A)

    @property
    def rts(self) -> float:
        """F2-7: √(√(Iy Cw) / Sx), or the table's."""
        return self.extra.get("rts") or math.sqrt(math.sqrt(self.Iy * self.Cw) / self.Sx)


# --- tension ----------------------------------------------------------------------------


def tension(p: Props, s: Steel, net_ratio: float = 1.0) -> list[Strength]:
    """D2: yielding on the gross area, rupture on the effective net area A_e = ratio A_g
    (the connection's holes and shear lag, D3, are the user's ratio)."""
    out = [Strength(s.Fy * p.A, 0.90, 1.67, "tensile yielding", "D2(a)")]
    out.append(Strength(s.Fu * net_ratio * p.A, 0.75, 2.00, "tensile rupture", "D2(b)",
                        (f"A_e = {net_ratio:g} A_g",)))  # fmt: skip
    return out


# --- compression ------------------------------------------------------------------------


def _fcr(Fy: float, Fe: float) -> float:
    """E3-2 and E3-3."""
    return Fy * 0.658 ** (Fy / Fe) if Fy / Fe <= 2.25 else 0.877 * Fe


def _effective(width: float, lam: float, lam_r: float, Fy: float, Fcr: float,
               c1: float, c2: float) -> float:  # fmt: skip
    """E7-2 and E7-3: an element's effective width."""
    if lam <= lam_r * math.sqrt(Fy / Fcr):
        return width
    fel = (c2 * lam_r / lam) ** 2 * Fy  # E7-5
    root = math.sqrt(fel / Fcr)
    return width * (1 - c1 * root) * root


def _compression_area(p: Props, s: Steel, Fcr: float) -> tuple[float, list[str]]:
    """A_e (E7): the gross area less each slender element's (b − b_e) t; round HSS by
    E7-7."""
    root = math.sqrt(s.E / s.Fy)
    notes, area = [], p.A
    if p.kind == "I":
        lam_f, lam_rf = p.bf / (2 * p.tf), 0.56 * root  # Table B4.1a case 1
        be = _effective(p.bf / 2, lam_f, lam_rf, s.Fy, Fcr, 0.22, 1.49)
        if be < p.bf / 2:
            area -= 4 * (p.bf / 2 - be) * p.tf
            notes.append(f"slender flanges: b_e {be / (p.bf / 2):.3f} b (E7)")
        lam_w, lam_rw = p.h / p.tw, 1.49 * root  # case 5
        he = _effective(p.h, lam_w, lam_rw, s.Fy, Fcr, 0.18, 1.31)
        if he < p.h:
            area -= (p.h - he) * p.tw
            notes.append(f"slender web: h_e {he / p.h:.3f} h (E7)")
    elif p.kind == "RHS":
        lam_r = 1.40 * root  # case 6
        for width, n in ((p.bf - 3 * p.t, 2), (p.d - 3 * p.t, 2)):
            be = _effective(width, width / p.t, lam_r, s.Fy, Fcr, 0.20, 1.38)
            if be < width:
                area -= n * (width - be) * p.t
                notes.append(f"slender walls: b_e {be / width:.3f} b (E7)")
    elif p.kind == "CHS":
        dt = p.d / p.t
        if dt > 0.45 * s.E / s.Fy:
            raise NotCovered(f"{p.designation}: D/t {dt:.1f} above 0.45 E/Fy, beyond E7.2")
        if dt > 0.11 * s.E / s.Fy:  # E7-7
            area = (0.038 * s.E / (s.Fy * dt) + 2 / 3) * p.A
            notes.append(f"slender wall: A_e {area / p.A:.3f} A_g (E7-7)")
    return area, notes


def compression(p: Props, s: Steel, Lcx: float, Lcy: float, Lcz: float) -> Strength:
    """E3 flexural buckling about each axis, E4(a) torsional buckling of a doubly
    symmetric I (Lcz past Lcy can govern), each with E7's effective area: the least."""
    candidates = []
    for name, lc, r in (("x", Lcx, p.rx), ("y", Lcy, p.ry)):
        fe = math.pi**2 * s.E / (lc / r) ** 2 if lc > 0 else math.inf  # E3-4
        candidates.append((f"flexural buckling about {name}", "E3", fe, lc / r))
    if p.kind == "I" and Lcz > 0:  # E4-2
        fe = (math.pi**2 * s.E * p.Cw / Lcz**2 + s.G * p.J) / (p.Ix + p.Iy)
        candidates.append(("torsional buckling", "E4(a)", fe, None))
    best = None
    for limit, clause, fe, slender in candidates:
        fcr = _fcr(s.Fy, fe) if math.isfinite(fe) else s.Fy
        area, notes = _compression_area(p, s, fcr)
        if area < p.A:
            clause = f"{clause}, E7"
        if slender is not None and slender > 200:
            notes = [*notes, f"L_c/r = {slender:.0f} above 200 (E2's user note)"]
        st = Strength(fcr * area, 0.90, 1.67, limit, clause, tuple(notes))
        if best is None or st.nominal < best.nominal:
            best = st
    assert best is not None
    return best


# --- flexure ----------------------------------------------------------------------------


def _ltb_i(p: Props, s: Steel, Lb: float, Cb: float) -> tuple[float, str, list[str]]:
    """F2.2: lateral-torsional buckling of a doubly symmetric I (c = 1)."""
    mp = s.Fy * p.Zx
    root = math.sqrt(s.E / s.Fy)
    lp = 1.76 * p.ry * root  # F2-5
    jc = p.J / (p.Sx * p.ho)
    lr = 1.95 * p.rts * s.E / (0.7 * s.Fy) * math.sqrt(
        jc + math.sqrt(jc**2 + 6.76 * (0.7 * s.Fy / s.E) ** 2))  # F2-6  # fmt: skip
    notes = [f"L_p {lp:.4g} m, L_r {lr:.4g} m, C_b {Cb:.3f}"]
    if Lb <= lp:
        return mp, "F2.1", notes
    if Lb <= lr:  # F2-2
        mn = Cb * (mp - (mp - 0.7 * s.Fy * p.Sx) * (Lb - lp) / (lr - lp))
        return min(mn, mp), "F2.2(b)", notes
    fcr = Cb * math.pi**2 * s.E / (Lb / p.rts) ** 2 * math.sqrt(1 + 0.078 * jc * (Lb / p.rts) ** 2)
    return min(fcr * p.Sx, mp), "F2.2(c)", notes


def flexure_x(p: Props, s: Steel, Lb: float, Cb: float) -> Strength:
    """Bending about the strong axis: the least of yielding, lateral-torsional buckling
    and local buckling for the section's kind."""
    root = math.sqrt(s.E / s.Fy)
    if p.kind == "I":
        if p.h / p.tw > 3.76 * root:
            raise NotCovered(f"{p.designation}: web noncompact in flexure (F4, F5 not built)")
        mn, clause, notes = _ltb_i(p, s, Lb, Cb)
        mp = s.Fy * p.Zx
        lam, lp, lr = p.bf / (2 * p.tf), 0.38 * root, 1.0 * root  # Table B4.1b case 10
        if lam > lp:
            if lam <= lr:  # F3-1
                flb = mp - (mp - 0.7 * s.Fy * p.Sx) * (lam - lp) / (lr - lp)
                fclause = "F3.2(a)"
            else:  # F3-2
                kc = min(max(4 / math.sqrt(p.h / p.tw), 0.35), 0.76)
                flb = 0.9 * s.E * kc * p.Sx / lam**2
                fclause = "F3.2(b)"
            if flb < mn:
                return Strength(flb, 0.90, 1.67, "compression flange local buckling", fclause,
                                (f"b_f/2t_f {lam:.2f} > λ_p {lp:.2f}", *notes))  # fmt: skip
        limit = "yielding" if clause == "F2.1" else "lateral-torsional buckling"
        return Strength(mn, 0.90, 1.67, limit, clause, tuple(notes))
    if p.kind == "RHS":
        return _rhs(p, s, Lb, Cb, major=True)
    if p.kind == "CHS":
        return _chs(p, s)
    if p.kind == "bar":
        return _bar(p, s, Lb, Cb)
    return _round(p, s)


def flexure_y(p: Props, s: Steel) -> Strength:
    """Bending about the weak axis: F6 for an I, F7/F8/F11 for the others."""
    root = math.sqrt(s.E / s.Fy)
    if p.kind == "I":  # F6
        mp = min(s.Fy * p.Zy, 1.6 * s.Fy * p.Sy)
        lam, lp, lr = p.bf / (2 * p.tf), 0.38 * root, 1.0 * root
        if lam <= lp:
            return Strength(mp, 0.90, 1.67, "yielding", "F6.1")
        if lam <= lr:  # F6-2
            mn = mp - (mp - 0.7 * s.Fy * p.Sy) * (lam - lp) / (lr - lp)
            return Strength(mn, 0.90, 1.67, "flange local buckling", "F6.2(a)")
        return Strength(0.69 * s.E / lam**2 * p.Sy, 0.90, 1.67, "flange local buckling",
                        "F6.2(b)")  # fmt: skip
    if p.kind == "RHS":
        return _rhs(p, s, 0.0, 1.0, major=False)
    if p.kind == "CHS":
        return _chs(p, s)
    if p.kind == "bar":
        return Strength(min(s.Fy * p.Zy, 1.6 * s.Fy * p.Sy), 0.90, 1.67, "yielding", "F11.1")
    return _round(p, s)


def _rhs(p: Props, s: Steel, Lb: float, Cb: float, major: bool) -> Strength:
    """F7: yielding, flange and web local buckling, and (about the strong axis) lateral-
    torsional buckling (F7.4)."""
    root = math.sqrt(s.E / s.Fy)
    if major:
        flange, web, z, sm, depth = p.bf, p.d, p.Zx, p.Sx, p.d
    else:
        flange, web, z, sm, depth = p.d, p.bf, p.Zy, p.Sy, p.bf
    mp = s.Fy * z
    best = Strength(mp, 0.90, 1.67, "yielding", "F7.1")
    b = flange - 3 * p.t
    lam = b / p.t
    if lam > 1.12 * root:  # Table B4.1b case 17
        if lam <= 1.40 * root:  # F7-2
            mn = min(mp - (mp - s.Fy * sm) * (3.57 * lam / root - 4.0), mp)
            st = Strength(mn, 0.90, 1.67, "flange local buckling", "F7.2(b)")
        else:  # F7-3, F7-4
            be = min(1.92 * p.t * root * (1 - 0.38 / lam * root), b)
            st = Strength(s.Fy * _rhs_se(flange, depth, p.t, b - be), 0.90, 1.67,
                          "flange local buckling", "F7.2(c)", (f"b_e {be / b:.3f} b",))  # fmt: skip
        best = min(best, st, key=lambda x: x.nominal)
    h = web - 3 * p.t
    lam_w = h / p.t
    if lam_w > 2.42 * root:  # case 19
        if lam_w > 5.70 * root:
            raise NotCovered(f"{p.designation}: slender webs in flexure (F7.3(b) not built)")
        mn = min(mp - (mp - s.Fy * sm) * (0.305 * lam_w / root - 0.738), mp)  # F7-5
        best = min(best, Strength(mn, 0.90, 1.67, "web local buckling", "F7.3(a)"),
                   key=lambda x: x.nominal)  # fmt: skip
    if major and Lb > 0 and p.d > p.bf:  # F7.4
        ry, rootja = p.ry, math.sqrt(p.J * p.A)
        lp = 0.13 * s.E * ry * rootja / mp  # F7-12
        lr = 2 * s.E * ry * rootja / (0.7 * s.Fy * p.Sx)  # F7-13
        if Lb > lp:
            if Lb <= lr:  # F7-10
                mn = Cb * (mp - (mp - 0.7 * s.Fy * p.Sx) * (Lb - lp) / (lr - lp))
            else:  # F7-11
                mn = 2 * s.E * Cb * rootja / (Lb / ry)
            st = Strength(min(mn, mp), 0.90, 1.67, "lateral-torsional buckling", "F7.4",
                          (f"L_p {lp:.4g} m, L_r {lr:.4g} m, C_b {Cb:.3f}",))  # fmt: skip
            best = min(best, st, key=lambda x: x.nominal)
    return best


def _rhs_se(width: float, depth: float, t: float, lost: float) -> float:
    """The effective section modulus of a box (walls t, the compression flange short of
    `lost` of its width), about its horizontal axis (F7.2(c)): to the compression edge."""
    parts = [  # (area, centroid from the bottom, own I)
        (width * t, depth - t / 2, width * t**3 / 12),
        (width * t, t / 2, width * t**3 / 12),
        (2 * (depth - 2 * t) * t, depth / 2, 2 * t * (depth - 2 * t) ** 3 / 12),
        (-lost * t, depth - t / 2, -lost * t**3 / 12),
    ]
    area = sum(a for a, _, _ in parts)
    ybar = sum(a * y for a, y, _ in parts) / area
    i = sum(i0 + a * (y - ybar) ** 2 for a, y, i0 in parts)
    return i / max(depth - ybar, ybar)


def _chs(p: Props, s: Steel) -> Strength:
    """F8: round HSS with D/t below 0.45 E/Fy."""
    dt, ef = p.d / p.t, s.E / s.Fy
    if dt > 0.45 * ef:
        raise NotCovered(f"{p.designation}: D/t {dt:.1f} above 0.45 E/Fy, beyond F8")
    mp = s.Fy * p.Zx
    if dt <= 0.07 * ef:
        return Strength(mp, 0.90, 1.67, "yielding", "F8.1")
    if dt <= 0.31 * ef:  # F8-2
        return Strength(min((0.021 * s.E / dt + s.Fy) * p.Sx, mp), 0.90, 1.67,
                        "local buckling", "F8.2(b)")  # fmt: skip
    return Strength(0.33 * s.E / dt * p.Sx, 0.90, 1.67, "local buckling", "F8.2(c)")


def _bar(p: Props, s: Steel, Lb: float, Cb: float) -> Strength:
    """F11: a solid rectangle bent about its strong axis (d across x, t = b along it)."""
    my, mp = s.Fy * p.Sx, min(s.Fy * p.Zx, 1.6 * s.Fy * p.Sx)
    slender = Lb * p.d / p.bf**2
    if slender <= 0.08 * s.E / s.Fy:
        return Strength(mp, 0.90, 1.67, "yielding", "F11.1")
    if slender <= 1.9 * s.E / s.Fy:  # F11-2
        mn = Cb * (1.52 - 0.274 * slender * s.Fy / s.E) * my
        return Strength(min(mn, mp), 0.90, 1.67, "lateral-torsional buckling", "F11.2(b)")
    fcr = 1.9 * s.E * Cb / slender  # F11-4
    return Strength(min(fcr * p.Sx, mp), 0.90, 1.67, "lateral-torsional buckling", "F11.2(c)")


def _round(p: Props, s: Steel) -> Strength:
    return Strength(min(s.Fy * p.Zx, 1.6 * s.Fy * p.Sx), 0.90, 1.67, "yielding", "F11.1")


def cb(moments: list[float]) -> float:
    """F1-1 from |M| along the unbraced segment (stations in order, its ends included):
    12.5 M_max / (2.5 M_max + 3 M_A + 4 M_B + 3 M_C), the quarter points interpolated."""
    m = [abs(v) for v in moments]
    mmax = max(m)
    if mmax == 0:
        return 1.0
    n = len(m) - 1

    def at(f: float) -> float:
        x = f * n
        k = min(int(x), n - 1)
        return m[k] + (m[k + 1] - m[k]) * (x - k)

    return cb_from(mmax, at(0.25), at(0.5), at(0.75))


def cb_from(mmax: float, ma: float, mb: float, mc: float) -> float:
    """F1-1 from the absolute maximum moment and the quarter points' ones."""
    if mmax == 0:
        return 1.0
    return 12.5 * mmax / (2.5 * mmax + 3 * abs(ma) + 4 * abs(mb) + 3 * abs(mc))


# --- shear and torsion ------------------------------------------------------------------


def _cv2(ratio: float, kv: float, s: Steel) -> float:
    """G2-9 to G2-11."""
    limit = math.sqrt(kv * s.E / s.Fy)
    if ratio <= 1.10 * limit:
        return 1.0
    if ratio <= 1.37 * limit:
        return 1.10 * limit / ratio
    return 1.51 * kv * s.E / (ratio**2 * s.Fy)


def shear_x(p: Props, s: Steel, length: float) -> Strength | None:
    """Shear along the web (with strong-axis bending): G2.1 for an I, G4 for RHS, G5 for
    round HSS; None for solid bars (Chapter G has no provision for them)."""
    root = math.sqrt(s.E / s.Fy)
    if p.kind == "I":
        aw, ratio = p.d * p.tw, p.h / p.tw
        if ratio <= 2.24 * root:  # G2.1(a): rolled I-shapes
            return Strength(0.6 * s.Fy * aw, 1.00, 1.50, "shear yielding", "G2.1(a)")
        kv = 5.34
        cv1 = 1.0 if ratio <= 1.10 * math.sqrt(kv * s.E / s.Fy) else (
            1.10 * math.sqrt(kv * s.E / s.Fy) / ratio)  # fmt: skip
        return Strength(0.6 * s.Fy * aw * cv1, 0.90, 1.67, "shear", "G2.1(b)")
    if p.kind == "RHS":
        return _rhs_shear(p.d, p, s)
    if p.kind == "CHS":
        return _chs_shear(p, s, length)
    return None


def shear_y(p: Props, s: Steel, length: float) -> Strength | None:
    """Shear along the flanges (with weak-axis bending): G6 for an I (both flanges)."""
    if p.kind == "I":
        cv2 = _cv2(p.bf / (2 * p.tf), 1.2, s)
        return Strength(2 * 0.6 * s.Fy * p.bf * p.tf * cv2, 0.90, 1.67, "shear", "G6")
    if p.kind == "RHS":
        return _rhs_shear(p.bf, p, s)
    if p.kind == "CHS":
        return _chs_shear(p, s, length)
    return None


def _rhs_shear(along: float, p: Props, s: Steel) -> Strength:
    """G4: the two walls along the shear, h = the wall's flat width (outside − 3t)."""
    h = along - 3 * p.t
    return Strength(0.6 * s.Fy * 2 * h * p.t * _cv2(h / p.t, 5.0, s), 0.90, 1.67, "shear",
                    "G4")  # fmt: skip


def _chs_shear(p: Props, s: Steel, length: float) -> Strength:
    """G5, L_v taken as half the member (zero shear at its middle, the most there is)."""
    dt, lv = p.d / p.t, max(length / 2, p.d)
    fcr = min(max(1.60 * s.E / (math.sqrt(lv / p.d) * dt**1.25), 0.78 * s.E / dt**1.5),
              0.6 * s.Fy)  # fmt: skip
    return Strength(fcr * p.A / 2, 0.90, 1.67, "shear", "G5", (f"L_v {lv:.4g} m",))


def torsion(p: Props, s: Steel, length: float) -> Strength | None:
    """H3.1 for HSS; None otherwise (an I's torsion is Design Guide 9's, not checked)."""
    root = math.sqrt(s.E / s.Fy)
    if p.kind == "RHS":
        c = 2 * (p.bf - p.t) * (p.d - p.t) * p.t - 4.5 * (4 - math.pi) * p.t**3
        h = max(p.bf, p.d) - 3 * p.t
        ratio = h / p.t
        if ratio <= 2.45 * root:  # H3-3
            fcr = 0.6 * s.Fy
        elif ratio <= 3.07 * root:  # H3-4
            fcr = 0.6 * s.Fy * 2.45 * root / ratio
        else:  # H3-5
            fcr = 0.458 * math.pi**2 * s.E / ratio**2
        return Strength(fcr * c, 0.90, 1.67, "torsion", "H3.1(b)")
    if p.kind == "CHS":
        dt = p.d / p.t
        c = math.pi * (p.d - p.t) ** 2 * p.t / 2
        fcr = min(max(1.23 * s.E / (math.sqrt(length / p.d) * dt**1.25), 0.60 * s.E / dt**1.5),
                  0.6 * s.Fy)  # fmt: skip
        return Strength(fcr * c, 0.90, 1.67, "torsion", "H3.1(a)")
    return None


# --- combined ---------------------------------------------------------------------------


def interaction(pr: float, pc: float, mrx: float, mcx: float, mry: float, mcy: float) -> float:
    """H1-1a and H1-1b: the ratio, ≤ 1 passes. Demands and strengths as magnitudes."""
    ratio_p = pr / pc if pc else 0.0
    bending = (mrx / mcx if mcx else 0.0) + (mry / mcy if mcy else 0.0)
    if ratio_p >= 0.2:
        return ratio_p + 8 / 9 * bending
    return ratio_p / 2 + bending


def interaction_torsion(pr, pc, mr, mc, vr, vc, tr, tc) -> float:
    """H3-6: (Pr/Pc + Mr/Mc) + (Vr/Vc + Tr/Tc)², used where Tr > 0.2 Tc (H3.2)."""
    return (pr / pc + mr / mc) + (vr / vc + tr / tc) ** 2


# --- sections ---------------------------------------------------------------------------


def props(section) -> Props:
    """A `BeamSection` as `Props`: a library section from its table (AISC v16.0, or EN
    with r, ho and rts worked out), or Puzzle's rectangle, circle, box and tube."""
    if section.shape == "library":
        return _library(section.standard, section.designation)
    if section.shape == "rectangle":
        b, d = section.width.to_si(), section.height.to_si()  # width along the first axis
        if d < b:
            raise NotCovered("a rectangle bent about its weak axis as its first: give the "
                             "height across the first axis as the larger")  # fmt: skip
        j = d * b**3 * (1 / 3 - 0.21 * b / d * (1 - b**4 / (12 * d**4)))
        return Props("bar", b * d, b * d**3 / 12, d * b**3 / 12, b * d**2 / 4, d * b**2 / 4,
                     b * d**2 / 6, d * b**2 / 6, j, d=d, bf=b, designation="rectangle")  # fmt: skip
    if section.shape == "circle":
        dia = section.diameter.to_si()
        r = dia / 2
        i = math.pi * r**4 / 4
        return Props("round", math.pi * r**2, i, i, dia**3 / 6, dia**3 / 6, i / r, i / r, 2 * i,
                     d=dia, designation="circle")  # fmt: skip
    if section.shape == "tube":
        dia, t = section.diameter.to_si(), section.thickness.to_si()
        ro, ri = dia / 2, dia / 2 - t
        i = math.pi * (ro**4 - ri**4) / 4
        z = (dia**3 - (dia - 2 * t) ** 3) / 6
        return Props("CHS", math.pi * (ro**2 - ri**2), i, i, z, z, i / ro, i / ro, 2 * i, d=dia,
                     t=t, designation="tube")  # fmt: skip
    w, h, t = section.width.to_si(), section.height.to_si(), section.thickness.to_si()
    a = w * h - (w - 2 * t) * (h - 2 * t)
    ix = (w * h**3 - (w - 2 * t) * (h - 2 * t) ** 3) / 12
    iy = (h * w**3 - (h - 2 * t) * (w - 2 * t) ** 3) / 12
    zx = (w * h**2 - (w - 2 * t) * (h - 2 * t) ** 2) / 4
    zy = (h * w**2 - (h - 2 * t) * (w - 2 * t) ** 2) / 4
    am = (w - t) * (h - t)
    j = 4 * am**2 * t / (2 * (w - t) + 2 * (h - t))
    return Props("RHS", a, ix, iy, zx, zy, ix / (h / 2), iy / (w / 2), j, d=h, bf=w, t=t,
                 h=h - 3 * t, designation="box")  # fmt: skip


def _library(standard: str, designation: str) -> Props:
    from puzzle.sections import _table

    row = _table(standard)[designation]
    shape = row["shape"]
    if shape not in ("I", "RHS", "CHS"):
        raise NotCovered(f"{designation}: {shape} sections aren't checked yet (E4 with a shear "
                         "centre off the centroid, F9 and F10)")  # fmt: skip
    common = dict(designation=designation,
                  extra={k: row[k] for k in ("rx", "ry", "rts") if row.get(k)})  # fmt: skip
    if shape == "I":
        d, bf, tf, tw = row["d"], row["bf"], row["tf"], row["tw"]
        k = row.get("kdes") or (tf + row.get("r", 0.0))
        ho = row.get("ho") or d - tf
        cw = row.get("Cw") or row["Iy"] * ho**2 / 4  # E4's user note
        return Props("I", row["A"], row["Ix"], row["Iy"], row["Zx"], row["Zy"], row["Sx"],
                     row["Sy"], row["J"], cw, d=d, bf=bf, tf=tf, tw=tw, h=d - 2 * k, ho=ho,
                     **common)  # fmt: skip
    t = row.get("tdes") or row["t"]
    if shape == "RHS":
        return Props("RHS", row["A"], row["Ix"], row["Iy"], row["Zx"], row["Zy"], row["Sx"],
                     row["Sy"], row["J"], d=row["Ht"], bf=row["B"], t=t, h=row["Ht"] - 3 * t,
                     **common)  # fmt: skip
    return Props("CHS", row["A"], row["Ix"], row["Iy"], row["Zx"], row["Zy"], row["Sx"],
                 row["Sy"], row["J"], d=row["OD"], t=t, **common)  # fmt: skip
