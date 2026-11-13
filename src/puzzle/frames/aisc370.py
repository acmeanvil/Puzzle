"""Stainless steel member strengths by AISC 370-21 (ADR 0079 step 5), in SI, from the
specification's own equations (read from its pages):

- tension: D2 (as 360's: yielding φ 0.90, rupture 0.75);
- compression: E3 with Table E3.1's curves (A: an I about its minor axis and the rest,
  B: an I about its major axis and round HSS, C: rectangular HSS) and E4-2 (curve A),
  with E7's effective widths (E7-3, k 0.425 or 4.00) or a round HSS's E7-8;
- flexure: F2 (L_p, L_y, L_r with Table F2.1's β_LT, β_p,LT, β_y,LT by alloy family,
  α_LT), F3's flange local buckling with S_xe, F6 (yielding, flange local buckling,
  S_ye), F7 for rectangular HSS (no lateral-torsional buckling there), F8, F9;
- shear: G2.1 (C_v1 up to 1.2: strain hardening), G3, G4 (C_vM, C_vL), G5;
- combined: H1-1, as 360's.

Not built: F4/F5 (noncompact or slender webs in flexure), F7.3(c) (slender HSS webs),
torsion (G8, G9), channels, tees and angles (E4-3, F10), Appendix 2's continuous
strength method. A section or case outside these is refused (`NotCovered`).
The alloy family follows the material's name: duplex for S32101, S32205, S32304, S82441
and the like ("duplex", 2101, 2205, 2304, 2507 in its name), else austenitic.
"""

from __future__ import annotations

import math

from puzzle.frames.aisc360 import NotCovered, Props, Steel, Strength
from puzzle.frames.aisc360 import interaction as interaction  # H1-1 is the same

CURVES = {  # Table E3.1: α, β0, β1, β2
    "A": (0.56, 0.759, 0.409, 0.690),
    "B": (0.58, 0.891, 0.455, 0.820),
    "C": (0.69, 1.195, 0.501, 0.820),
}
LTB = {"austenitic": (0.82, 0.90, 0.40), "duplex": (0.86, 1.10, 0.50)}  # Table F2.1
DUPLEX = ("duplex", "2101", "2205", "2304", "2507", "32101", "32205", "32304", "82441",
          "31803", "32750")  # fmt: skip
NU = 0.3


def family(material) -> str:
    """austenitic or duplex, from the material's name (and description)."""
    text = f"{getattr(material, 'name', '')} {getattr(material, 'description', '') or ''}"
    return "duplex" if any(k in text.lower() for k in DUPLEX) else "austenitic"


# --- compression ------------------------------------------------------------------------


def fcr(fy: float, fe: float, curve: str) -> float:
    """E3-2 to E3-4."""
    alpha, b0, b1, b2 = CURVES[curve]
    ratio = fy / fe
    if ratio <= (b0 / math.pi) ** 2:
        return fy
    if ratio <= 3.20:
        return 1.2 * b1 ** (ratio**alpha) * fy
    return b2 * fe


def _fel(lam: float, k: float, e: float) -> float:
    """E7-4 / F3-4: an element's elastic local buckling stress."""
    return k * math.pi**2 * e / (12 * (1 - NU**2)) / lam**2


def _be(width: float, lam: float, lam_r: float, fy: float, f: float, k: float, e: float) -> float:
    """E7-2 and E7-3 with f = F_cr, or F3-3 with f = F_y (the flange slender past λ_r)."""
    if lam <= lam_r * math.sqrt(fy / f):
        return width
    fel = _fel(lam, k, e)
    root = math.sqrt(fel / f)
    return min(0.772 * width * (1 - 0.10 * root) * root, width)


def _area(p: Props, s: Steel, f: float) -> tuple[float, list[str]]:
    root = math.sqrt(s.E / s.Fy)
    area, notes = p.A, []
    if p.kind == "I":
        lam = p.bf / (2 * p.tf)
        be = _be(p.bf / 2, lam, 0.41 * root, s.Fy, f, 0.425, s.E)
        if be < p.bf / 2:
            area -= 4 * (p.bf / 2 - be) * p.tf
            notes.append(f"slender flanges: b_e {be / (p.bf / 2):.3f} b (E7-3)")
        lam_w = p.h / p.tw
        he = _be(p.h, lam_w, 1.24 * root, s.Fy, f, 4.0, s.E)
        if he < p.h:
            area -= (p.h - he) * p.tw
            notes.append(f"slender web: h_e {he / p.h:.3f} h (E7-3)")
    elif p.kind == "RHS":
        for width in (p.bf - 3 * p.t, p.d - 3 * p.t):
            be = _be(width, width / p.t, 1.24 * root, s.Fy, f, 4.0, s.E)
            if be < width:
                area -= 2 * (width - be) * p.t
                notes.append(f"slender walls: b_e {be / width:.3f} b (E7-3)")
    elif p.kind == "CHS":
        lam, lam_r = p.d / p.t, 0.10 * s.E / s.Fy
        if lam > lam_r * s.Fy / f:
            if lam >= 2.8 * lam_r * s.Fy / f:
                raise NotCovered(f"{p.designation}: D/t {lam:.1f} past E7.2's range")
            area = (0.6 * lam_r / lam * s.Fy / f + 2 / 5) * p.A  # E7-8
            notes.append(f"slender wall: A_e {area / p.A:.3f} A_g (E7-8)")
    return area, notes


def compression(p: Props, s: Steel, lcx: float, lcy: float, lcz: float) -> Strength:
    """E3 about each axis on its curve, E4-2 for an I (curve A), with E7: the least."""
    x_curve = {"I": "B", "RHS": "C", "CHS": "B"}.get(p.kind, "A")
    y_curve = {"I": "A", "RHS": "C", "CHS": "B"}.get(p.kind, "A")
    cases = []
    for name, lc, r, curve in (("x", lcx, p.rx, x_curve), ("y", lcy, p.ry, y_curve)):
        fe = math.pi**2 * s.E / (lc / r) ** 2 if lc > 0 else math.inf  # E3-5
        cases.append((f"flexural buckling about {name}", f"E3 (curve {curve})", fe, curve))
    if p.kind == "I" and lcz > 0:  # E4-2
        fe = (math.pi**2 * s.E * p.Cw / lcz**2 + s.G * p.J) / (p.Ix + p.Iy)
        cases.append(("torsional buckling", "E4(a) (curve A)", fe, "A"))
    best = None
    for limit, clause, fe, curve in cases:
        f = fcr(s.Fy, fe, curve) if math.isfinite(fe) else s.Fy
        area, notes = _area(p, s, f)
        st = Strength(f * area, 0.90, 1.67, limit, clause + (", E7" if area < p.A else ""),
                      tuple(notes))  # fmt: skip
        if best is None or st.nominal < best.nominal:
            best = st
    assert best is not None
    return best


# --- effective section moduli -----------------------------------------------------------


def _modulus(rects: list[tuple[float, float, float, float, float]], compression_top: bool) -> float:
    """S to the compression edge of rectangles (x0, x1, y0, y1, sign: +1 material, -1 a
    hole), bending about the horizontal axis through their centroid."""
    area = sum(s * (x1 - x0) * (y1 - y0) for x0, x1, y0, y1, s in rects)
    ybar = sum(s * (x1 - x0) * (y1 - y0) * (y0 + y1) / 2 for x0, x1, y0, y1, s in rects) / area
    i = sum(s * (x1 - x0) * (y1 - y0) * ((y1 - y0) ** 2 / 12 + ((y0 + y1) / 2 - ybar) ** 2)
            for x0, x1, y0, y1, s in rects)  # fmt: skip
    top = max(y1 for _x0, _x1, _y0, y1, s in rects if s > 0)
    bottom = min(y0 for _x0, _x1, y0, _y1, s in rects if s > 0)
    return i / ((top - ybar) if compression_top else (ybar - bottom))


def _i_sxe(p: Props, lost: float) -> float:
    """S_xe of an I whose compression (top) flange lost `lost` at each of its two tips."""
    d, bf, tf, tw = p.d, p.bf, p.tf, p.tw
    rects = [(-bf / 2, bf / 2, d - tf, d, 1), (-bf / 2, bf / 2, 0, tf, 1),
             (-tw / 2, tw / 2, tf, d - tf, 1), (-bf / 2, -bf / 2 + lost, d - tf, d, -1),
             (bf / 2 - lost, bf / 2, d - tf, d, -1)]  # fmt: skip
    return _modulus(rects, True)


def _i_sye(p: Props, lost: float) -> float:
    """S_ye of an I in minor-axis bending, its flanges' tips on the compression side
    short of `lost` (the section turned: the flanges vertical)."""
    d, bf, tf, tw = p.d, p.bf, p.tf, p.tw
    rects = [(0, tf, -bf / 2, bf / 2, 1), (d - tf, d, -bf / 2, bf / 2, 1),
             (tf, d - tf, -tw / 2, tw / 2, 1), (0, tf, bf / 2 - lost, bf / 2, -1),
             (d - tf, d, bf / 2 - lost, bf / 2, -1)]  # fmt: skip
    return _modulus(rects, True)


def _box_se(width: float, depth: float, t: float, lost: float) -> float:
    rects = [(0, width, depth - t, depth, 1), (0, width, 0, t, 1),
             (0, t, t, depth - t, 1), (width - t, width, t, depth - t, 1),
             (width / 2 - lost / 2, width / 2 + lost / 2, depth - t, depth, -1)]  # fmt: skip
    return _modulus(rects, True)


# --- flexure ----------------------------------------------------------------------------


def lengths(p: Props, s: Steel, alloy: str) -> tuple[float, float, float]:
    """L_p, L_y, L_r (F2-7 to F2-9), c = 1."""
    b_lt, b_p, b_y = LTB[alloy]
    jc = p.J / (p.Sx * p.ho)
    lp = b_p * p.rts * math.sqrt(s.E / s.Fy)
    ly = 1.95 * b_y * p.rts * b_lt * s.E / s.Fy * math.sqrt(
        jc + math.sqrt(jc**2 + 6.76 * (s.Fy / (b_lt * s.E)) ** 2))  # fmt: skip
    lr = 1.95 * p.rts * b_lt * s.E / (0.30 * s.Fy) * math.sqrt(
        jc + math.sqrt(jc**2 + 6.76 * (0.30 * s.Fy / (b_lt * s.E)) ** 2))  # fmt: skip
    return lp, ly, lr


def _ltb(p: Props, s: Steel, lb: float, cb: float, alloy: str) -> tuple[float, str, list[str]]:
    mp, my = s.Fy * p.Zx, s.Fy * p.Sx
    lp, ly, lr = lengths(p, s, alloy)
    notes = [f"L_p {lp:.4g} m, L_y {ly:.4g} m, L_r {lr:.4g} m, C_b {cb:.3f}"]
    if lb <= lp:
        return mp, "F2.1", notes
    if lb <= ly:  # F2-2
        return min(cb * (mp - (mp - my) * (lb - lp) / (ly - lp)), mp), "F2.2(b)", notes
    if lb <= lr:  # F2-3, F2-6
        a_lt = 0.60 - 0.40 * (lb - ly) / (lr - ly)
        mn = cb * (my - (my - 0.30 * my) * ((lb - ly) / (lr - ly)) ** a_lt)
        return min(mn, mp), "F2.2(c)", notes
    jc = p.J / (p.Sx * p.ho)
    f = cb * math.pi**2 * s.E / (lb / p.rts) ** 2 * math.sqrt(1 + 0.078 * jc * (lb / p.rts) ** 2)
    return min(LTB[alloy][0] * f * p.Sx, mp), "F2.2(d)", notes  # F2-4, F2-5


def flexure_x(p: Props, s: Steel, lb: float, cb: float, alloy: str = "austenitic") -> Strength:
    root = math.sqrt(s.E / s.Fy)
    if p.kind == "I":
        if p.h / p.tw > 2.54 * root:
            raise NotCovered(f"{p.designation}: web noncompact in flexure (F4, F5 not built)")
        mn, clause, notes = _ltb(p, s, lb, cb, alloy)
        mp = s.Fy * p.Zx
        lam, lp, lr = p.bf / (2 * p.tf), 0.33 * root, 0.41 * root  # Table B4.1b case 4
        if lam > lp:
            if lam <= lr:  # F3-1
                flb, fclause = mp - (mp - s.Fy * p.Sx) * (lam - lp) / (lr - lp), "F3.2(a)"
            else:  # F3-2, F3-3
                be = _be(p.bf / 2, lam, lr, s.Fy, s.Fy, 0.425, s.E)
                flb, fclause = s.Fy * _i_sxe(p, p.bf / 2 - be), "F3.2(b)"
            if flb < mn:
                return Strength(flb, 0.90, 1.67, "compression flange local buckling",
                                fclause, tuple(notes))  # fmt: skip
        limit = "yielding" if clause == "F2.1" else "lateral-torsional buckling"
        return Strength(mn, 0.90, 1.67, limit, clause, tuple(notes))
    if p.kind == "RHS":
        return _rhs(p, s, major=True)
    if p.kind == "CHS":
        return _chs(p, s)
    if p.kind == "bar":
        return _bar(p, s, lb, cb)
    return Strength(s.Fy * p.Zx, 0.90, 1.67, "yielding", "F9.1")


def flexure_y(p: Props, s: Steel) -> Strength:
    root = math.sqrt(s.E / s.Fy)
    if p.kind == "I":  # F6
        mp = s.Fy * p.Zy
        lam, lp, lr = p.bf / (2 * p.tf), 0.33 * root, 0.41 * root
        if lam <= lp:
            return Strength(mp, 0.90, 1.67, "yielding", "F6.1")
        if lam <= lr:  # F6-2
            mn = mp - (mp - s.Fy * p.Sy) * (lam - lp) / (lr - lp)
            return Strength(mn, 0.90, 1.67, "flange local buckling", "F6.2(b)")
        be = _be(p.bf / 2, lam, lr, s.Fy, s.Fy, 0.425, s.E)
        return Strength(s.Fy * _i_sye(p, p.bf / 2 - be), 0.90, 1.67, "flange local buckling",
                        "F6.2(c)")  # fmt: skip
    if p.kind == "RHS":
        return _rhs(p, s, major=False)
    if p.kind == "CHS":
        return _chs(p, s)
    return Strength(s.Fy * p.Zy, 0.90, 1.67, "yielding", "F9.1")


def _rhs(p: Props, s: Steel, major: bool) -> Strength:
    """F7: yielding, flange local buckling (F7-2, F7-3 with F7-4's b_e for cold-formed
    HSS), web local buckling (F7-5); no lateral-torsional buckling."""
    root = math.sqrt(s.E / s.Fy)
    flange, web, z, sm = (p.bf, p.d, p.Zx, p.Sx) if major else (p.d, p.bf, p.Zy, p.Sy)
    mp = s.Fy * z
    best = Strength(mp, 0.90, 1.67, "yielding", "F7.1")
    b = flange - 3 * p.t
    lam, lp, lr = b / p.t, 1.17 * root, 1.24 * root  # Table B4.1b case 6
    if lam > lp:
        if lam <= lr:
            st = Strength(min(mp - (mp - s.Fy * sm) * (lam - lp) / (lr - lp), mp), 0.90, 1.67,
                          "flange local buckling", "F7.2(b)")  # fmt: skip
        else:
            fel = _fel(lam, 4.0, s.E)
            root_e = math.sqrt(fel / s.Fy)
            be = min(b * (1 - 0.22 * root_e) * root_e, b)  # F7-4
            st = Strength(s.Fy * _box_se(flange, web, p.t, b - be), 0.90, 1.67,
                          "flange local buckling", "F7.2(c)", (f"b_e {be / b:.3f} b",))  # fmt: skip
        best = min(best, st, key=lambda x: x.nominal)
    h = web - 3 * p.t
    lam_w, lpw, lrw = h / p.t, 2.54 * root, 3.01 * root  # case 5
    if lam_w > lpw:
        if lam_w > lrw:
            raise NotCovered(f"{p.designation}: slender webs in flexure (F7.3(c) not built)")
        mn = min(mp - (mp - s.Fy * sm) * (lam_w - lpw) / (lrw - lpw), mp)
        best = min(best, Strength(mn, 0.90, 1.67, "web local buckling", "F7.3(b)"),
                   key=lambda x: x.nominal)  # fmt: skip
    return best


def _chs(p: Props, s: Steel) -> Strength:
    lam, ef = p.d / p.t, s.E / s.Fy
    if lam <= 0.08 * ef:
        return Strength(s.Fy * p.Zx, 0.90, 1.67, "yielding", "F8.1")
    if lam <= 0.31 * ef:  # F8-2
        mn = min((0.068 * 0.31 * ef / lam + 1) * s.Fy * p.Sx, s.Fy * p.Zx)
        return Strength(mn, 0.90, 1.67, "local buckling", "F8.2(b)")
    raise NotCovered(f"{p.designation}: D/t {lam:.1f} slender in flexure (beyond F8)")


def _bar(p: Props, s: Steel, lb: float, cb: float) -> Strength:
    """F9 for a solid rectangle about its major axis (d across x, t = b along it)."""
    my, mp = s.Fy * p.Sx, s.Fy * p.Zx
    slender = lb * p.d / p.bf**2
    if slender <= 0.306 * s.E / s.Fy:
        return Strength(mp, 0.90, 1.67, "yielding", "F9.1")
    if slender <= 2.00 * s.E / s.Fy:  # F9-2
        mn = cb * (1.61 - 0.36 * slender * s.Fy / s.E) * my
        return Strength(min(mn, mp), 0.90, 1.67, "lateral-torsional buckling", "F9.2(b)")
    f = 1.78 * s.E * cb / slender  # F9-4
    return Strength(min(f * p.Sx, mp), 0.90, 1.67, "lateral-torsional buckling", "F9.2(c)")


# --- shear ------------------------------------------------------------------------------


def cv2(lam: float, kv: float, s: Steel) -> float:
    """G2-8 to G2-11."""
    r = math.sqrt(kv * s.E / s.Fy)
    if lam <= 0.33 * r:
        return 1.2
    if lam <= 0.97 * r:
        return 1.2 - 0.62 * (lam / r - 0.33)
    if lam <= 2.68 * r:
        return (5.02 * r - lam) / (1.62 * r + 3.55 * lam)
    return 1.51 * kv * s.E / (lam**2 * s.Fy)


def shear_x(p: Props, s: Steel, length: float) -> Strength | None:
    if p.kind == "I":  # G2.1
        lam, r = p.h / p.tw, math.sqrt(5.34 * s.E / s.Fy)
        cv1 = 1.2 if lam <= 0.59 * r else 1.55 * r / (0.7 * r + lam)
        return Strength(0.6 * s.Fy * p.d * p.tw * cv1, 0.90, 1.67, "shear", "G2.1")
    if p.kind == "RHS":
        return _rhs_shear(p.d, p, s)
    if p.kind == "CHS":
        return _chs_shear(p, s, length)
    if p.kind == "bar":  # G6: one stiffened element, the whole section
        return Strength(0.6 * cv2(p.d / p.bf, 5.0, s) * s.Fy * p.d * p.bf, 0.90, 1.67, "shear",
                        "G6")  # fmt: skip
    return None


def shear_y(p: Props, s: Steel, length: float) -> Strength | None:
    if p.kind == "I":  # G5, both flanges
        c = cv2(p.bf / (2 * p.tf), 1.2, s)
        return Strength(2 * 0.6 * s.Fy * p.bf * p.tf * c, 0.90, 1.67, "shear", "G5")
    if p.kind == "RHS":
        return _rhs_shear(p.bf, p, s)
    if p.kind == "CHS":
        return _chs_shear(p, s, length)
    if p.kind == "bar":
        return Strength(0.6 * cv2(p.bf / p.d, 5.0, s) * s.Fy * p.d * p.bf, 0.90, 1.67, "shear",
                        "G6")  # fmt: skip
    return None


def _rhs_shear(along: float, p: Props, s: Steel) -> Strength:
    h = along - 3 * p.t  # G3: the outside dimension less 3t, the corner radius unknown
    return Strength(0.6 * s.Fy * 2 * h * p.t * cv2(h / p.t, 5.0, s), 0.90, 1.67, "shear", "G3")


def _chs_shear(p: Props, s: Steel, length: float) -> Strength:
    """G4, L_v half the member."""
    lam, ef = p.d / p.t, s.E / s.Fy
    lv = max(length / 2, p.d)
    root = math.sqrt(lv / p.d)
    if lv / p.d <= 4.21 * math.sqrt(lam):  # G4-2, else G4-3
        limit = (ef / root) ** 0.80
        if lam <= 0.32 * limit:
            c = 1.0
        elif lam <= 4.65 * limit:
            c = 1 - 0.61 * (0.47 * math.sqrt(root * lam**1.25 / ef) - 0.23)
        else:
            c = 2.67 * s.E / (root * lam**1.25 * s.Fy)
        clause = "G4 (C_vM)"
    else:
        if lam <= 0.24 * ef**0.67:
            c = 1.0
        elif lam <= 2.23 * ef**0.67:
            c = 1 - 0.61 * (0.68 * math.sqrt(lam**1.50 / ef) - 0.23)
        else:
            c = 1.30 * s.E / (lam**1.50 * s.Fy)
        clause = "G4 (C_vL)"
    return Strength(0.6 * c * s.Fy * p.A / 2, 0.90, 1.67, "shear", clause, (f"L_v {lv:.4g} m",))


def tension(p: Props, s: Steel, net_ratio: float = 1.0) -> list[Strength]:
    return [Strength(s.Fy * p.A, 0.90, 1.67, "tensile yielding", "D2(a)"),
            Strength(s.Fu * net_ratio * p.A, 0.75, 2.00, "tensile rupture", "D2(b)",
                     (f"A_e = {net_ratio:g} A_g",))]  # fmt: skip


def torsion(p: Props, s: Steel, length: float) -> None:
    """G8/G9 are not built: torsion isn't checked on stainless members."""
    return None
