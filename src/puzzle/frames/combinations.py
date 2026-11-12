"""Load combinations (ADR 0079 step 2): ASCE 7-16 Chapter 2's, from the load cases a
frame has, and AISC 360's notional loads for the Direct Analysis Method.

Cases are named by ASCE 7's symbols: D dead, L live, Lr roof live, S snow, R rain, W wind,
E seismic (load effects as given; their reverse sign is a combination of its own), plus
any name of your own, which no generated combination uses. "Lr or S or R" makes one
combination per case the frame has; a combination none of whose variable cases the frame
has is left out (1.4D always stays).
"""

from __future__ import annotations

import itertools

ROOF = ("Lr", "S", "R")
SYMBOLS = ("D", "L", "Lr", "S", "R", "W", "E")

# (name, {case: factor}) templates: "roof" stands for each of Lr, S, R in turn.
LRFD = [  # ASCE 7-16 2.3.1
    ("1.4D", {"D": 1.4}),
    ("1.2D + 1.6L + 0.5{roof}", {"D": 1.2, "L": 1.6, "roof": 0.5}),
    ("1.2D + 1.6{roof} + L", {"D": 1.2, "roof": 1.6, "L": 1.0}),
    ("1.2D + 1.6{roof} + 0.5W", {"D": 1.2, "roof": 1.6, "W": 0.5}),
    ("1.2D + 1.0W + L + 0.5{roof}", {"D": 1.2, "W": 1.0, "L": 1.0, "roof": 0.5}),
    ("1.2D + 1.0E + L + 0.2S", {"D": 1.2, "E": 1.0, "L": 1.0, "S": 0.2}),
    ("0.9D + 1.0W", {"D": 0.9, "W": 1.0}),
    ("0.9D + 1.0E", {"D": 0.9, "E": 1.0}),
]
ASD = [  # ASCE 7-16 2.4.1
    ("D", {"D": 1.0}),
    ("D + L", {"D": 1.0, "L": 1.0}),
    ("D + {roof}", {"D": 1.0, "roof": 1.0}),
    ("D + 0.75L + 0.75{roof}", {"D": 1.0, "L": 0.75, "roof": 0.75}),
    ("D + 0.6W", {"D": 1.0, "W": 0.6}),
    ("D + 0.7E", {"D": 1.0, "E": 0.7}),
    ("D + 0.75L + 0.75(0.6W) + 0.75{roof}", {"D": 1.0, "L": 0.75, "W": 0.45, "roof": 0.75}),
    ("D + 0.75L + 0.75(0.7E) + 0.75S", {"D": 1.0, "L": 0.75, "E": 0.525, "S": 0.75}),
    ("0.6D + 0.6W", {"D": 0.6, "W": 0.6}),
    ("0.6D + 0.7E", {"D": 0.6, "E": 0.7}),
]
REVERSIBLE = ("W", "E")  # their effects act either way


def asce7(cases, method: str = "LRFD") -> list[tuple[str, dict[str, float]]]:
    """The combinations of `cases` (the names a frame's loads use) by ASCE 7-16's LRFD
    (2.3.1) or ASD (2.4.1): [(name, {case: factor})], without repeats."""
    have = set(cases)
    if "D" not in have:
        raise ValueError("ASCE 7's combinations start from the dead load: name a case D")
    templates = {"LRFD": LRFD, "ASD": ASD}[method.upper()]
    out: list[tuple[str, dict[str, float]]] = []
    seen: set[tuple] = set()
    for _name, factors in templates:
        if any(c in factors and c not in have for c in REVERSIBLE):
            continue  # a wind or seismic combination without that load is no combination
        if factors.get("roof", 0) >= 1.0 and not any(r in have for r in ROOF):
            continue  # led by the roof load (1.6Lr, D + Lr), which the frame hasn't
        roofs = [r for r in ROOF if r in have] if "roof" in factors else [None]
        for roof in roofs or [None]:
            combo = {}
            for case, factor in factors.items():
                key = roof if case == "roof" else case
                if key is not None and key in have:
                    combo[key] = factor
            variable = [c for c in factors if c not in ("D",)]
            if variable and not any((roof if c == "roof" else c) in combo for c in variable):
                continue  # none of its variable loads here: it is a D combination again
            for signs in _signs(combo):
                signed = {c: f * signs.get(c, 1.0) for c, f in combo.items()}
                key = tuple(sorted(signed.items()))
                if key in seen:
                    continue
                seen.add(key)
                out.append((_tidy(signed), signed))
    return out


def _signs(combo: dict[str, float]) -> list[dict[str, float]]:
    """Wind and seismic both ways: one sign dictionary per combination."""
    turning = [c for c in REVERSIBLE if c in combo]
    if not turning:
        return [{}]
    return [dict(zip(turning, s, strict=True))
            for s in itertools.product((1.0, -1.0), repeat=len(turning))]  # fmt: skip


def _tidy(factors: dict[str, float]) -> str:
    """The name written from the factors actually used (a template's absent cases
    dropped): 1.2D + 1.6L + 0.5S."""
    parts = []
    for case in sorted(factors, key=lambda c: SYMBOLS.index(c) if c in SYMBOLS else 99):
        f = factors[case]
        text = f"{abs(f):g}{case}" if abs(f) != 1.0 else case
        parts.append(("- " if f < 0 else "+ ") + text)
    joined = " ".join(parts)
    return joined[2:] if joined.startswith("+ ") else "-" + joined[2:]


def notional(gravity: dict[str, float], fraction: float = 0.002) -> dict[str, float]:
    """AISC 360 C2.2b's notional load at each node, N_i = 0.002 Y_i: `gravity` is each
    node's factored gravity load (N, downward positive); the result the lateral load."""
    return {node: fraction * y for node, y in gravity.items()}


def cases_of(component) -> list[str]:
    """The load cases a component's loads use, in ASCE 7's order then the rest."""
    found = {f.case for ph in component.physics for f in ph.features if f.case}
    return sorted(found, key=lambda c: (SYMBOLS.index(c) if c in SYMBOLS else 99, c))


def combination_ops(study, combos) -> list:
    """The Edit ops setting a study's combinations [(name, factors)]."""
    from puzzle.commands.model import EditOp

    value = [{"name": n, "factors": dict(f)} for n, f in combos]
    return [EditOp(op="set", path="combinations", value=value)]
