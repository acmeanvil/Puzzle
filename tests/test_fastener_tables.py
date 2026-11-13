"""Standard fastener dimensions (ADR 0050) from Lacewing Lab's tables: the values as the
standards print them, the parts a bolt check picks, ISO 273's holes, Shigley's thread
table against `bolts.SIZES`' stress areas, and the sizes added to `bolts.SIZES` against
the tables they came from."""

from __future__ import annotations

import pytest

from puzzle import bolts, fasteners

MM, IN = 1e-3, 0.0254


def test_tables_load_whole():
    every = fasteners.tables()
    assert len(every) == 37
    assert {t.kind for t in every} == {"screw", "nut", "washer"}
    assert len(fasteners.tables("washer")) == 14
    for t in every:
        assert t.standard and t.reference, t.id
        for r in t.rows:
            assert len(r.values) == len(t.columns), (t.id, r.size)
        if t.nut is not None:
            assert fasteners.table(t.nut).kind == "nut"
    with pytest.raises(KeyError):
        fasteners.table("iso.nothing")


@pytest.mark.parametrize(
    ("table", "size", "heading", "printed", "unit"),
    [
        # ISO 4014/4017 hex bolts (Lindstrom; dw from ASME B18.2.3.1M), M10
        ("iso.hexBolt", "M10", "s max", 16.0, MM),
        ("iso.hexBolt", "M10", "s min", 15.73, MM),
        ("iso.hexBolt", "M10", "k", 6.4, MM),
        ("iso.hexBolt", "M10", "dw min", 14.6, MM),
        ("iso.hexNut", "M12", "m max", 10.80, MM),
        ("iso.hexNut", "M64", "P fine", 4, MM),
        ("iso.socketHeadCapScrew", "M12", "dk min", 17.73, MM),
        # ASME B18.2.1 Table 2 hex bolts and B18.2.2 Table 4 hex nuts, 1/2 in
        ("asme.hexBolt", "1/2", "F", 0.75, IN),
        ("asme.hexBolt", "1/2", "H", 0.34375, IN),
        ("asme.hexBolt", "4", "UNC", 4, 1),
        ("asme.hexNut", "1/2", "H max", 0.448, IN),
        # ISO 7089 normal washer M10; ASTM F436 1/2 in
        ("iso.washerNormal", "M10", "d1", 10.5, MM),
        ("iso.washerNormal", "M10", "d2 min", 19.48, MM),
        ("astm.f436Washer", "1/2", "C max", 0.177, IN),
    ],
)
def test_values_as_printed(table, size, heading, printed, unit):
    t = fasteners.table(table)
    assert t.dimensions(t.row(size))[heading] == pytest.approx(printed * unit)


def test_rows_know_their_nominal_diameter():
    assert fasteners.table("iso.hexNut").row("M1.6").d == pytest.approx(1.6 * MM)
    assert fasteners.table("asme.hexBolt").row("1 1/8").d == pytest.approx(1.125 * IN)
    assert fasteners.table("asme.socketHeadCapScrew").row("#10").d == pytest.approx(0.190 * IN)
    assert fasteners.table("asme.hexBolt").row("4").d == pytest.approx(4 * IN)
    assert fasteners.table("asme.washerTypeAOther").rows[0].d is None  # named by its sizes


def test_parts_for_a_bolt():
    hex10 = fasteners.head("hex", 10 * MM)
    assert (hex10.table, hex10.size) == ("iso.hexBolt", "M10")
    assert hex10.bearing == pytest.approx(14.6 * MM)  # the washer face dw
    assert hex10.height == pytest.approx(6.58 * MM)  # k max
    assert hex10.across_flats == pytest.approx(15.73 * MM)
    socket = fasteners.head("socket", 12 * MM)
    assert socket.bearing == pytest.approx(17.73 * MM)  # no dw tabulated: dk min
    half = fasteners.head("hex", 0.5 * IN, metric=False)
    assert half.bearing == pytest.approx(0.725 * IN)  # F min
    assert fasteners.nut("hex", 0.5 * IN, metric=False).height == pytest.approx(0.448 * IN)
    assert fasteners.nut("heavy_hex", 20 * MM).bearing is not None
    w = fasteners.washer("normal", 10 * MM)
    assert (w.inner, w.bearing, w.height) == pytest.approx((10.5 * MM, 19.48 * MM, 2.2 * MM))
    assert fasteners.washer("structural", 10 * MM) is None  # F436 is inch only
    assert fasteners.head("hex", 11 * MM) is None  # not a standard size
    with pytest.raises(KeyError):
        fasteners.head("carriage", 10 * MM)


def test_iso_273_medium_holes():
    hole = fasteners.clearance_hole
    assert hole(12 * MM) == pytest.approx(13.5 * MM)
    assert hole(20 * MM) == pytest.approx(22 * MM)
    # 1/4 in = 6.35 mm, between M6 (0.6 mm clearance) and M8 (1.0 mm), as Lacewing Lab's
    # bolt_torque_test.dart has it.
    assert hole(6.35 * MM) == pytest.approx((6.35 + 0.6 + 0.35 / 2 * 0.4) * MM)
    for s in bolts.SIZES:
        assert hole(s.diameter) > s.diameter, s.name


def test_shigley_stress_areas_match_the_formulas_in_bolts():
    """Shigley Tables 8-1/8-2 tabulate At; bolts.SIZES computes it by ISO's and ASME's
    formulas. Every size in both agrees to 0.5 %."""
    by_name = {s.name: s for s in bolts.SIZES}
    matched = 0
    for t in fasteners.thread_sizes():
        if t.series.startswith("iso"):
            d, p = t.d / MM, t.pitch / MM
            name = f"M{d:g}" if t.series == "iso_coarse" else f"M{d:g}x{p:g}"
        else:
            name = t.label
        s = by_name.get(name)
        if s is None or abs(s.pitch - t.pitch) > 1e-9:
            continue
        assert s.stress_area == pytest.approx(t.stress_area, rel=0.005), name
        matched += 1
    assert matched >= 50
    assert fasteners.references()["threads"].startswith("Budynas")


def test_added_sizes_follow_their_tables():
    bolt, nut = fasteners.table("iso.hexBolt"), fasteners.table("iso.hexNut")
    for d, pc, pf, dw in bolts._METRIC_LARGE + [x for x in bolts._METRIC if x[0] >= 27]:
        b, n = bolt.at(d * MM), nut.at(d * MM)
        assert n.values[1] == pytest.approx(pc * MM) and n.values[2] == pytest.approx(pf * MM)
        # ISO 4014 product grade B: dw min = 0.95 s min (M27-M36 were already so)
        assert dw == pytest.approx(0.95 * bolt.value(b, "acrossFlats", "min") / MM, abs=0.006)
    fine = {(d, p) for d, p, _ in bolts._METRIC_FINE_MORE}
    assert {(18, 2.0), (20, 2.0), (22, 2.0)} <= {
        (round(r.d / MM), r.values[2] / MM) for r in nut.rows if r.values[2]
    }
    assert any("M10 × 1, M12 × 1.25" in note for note in nut.notes)
    assert {(10, 1.0), (12, 1.25)} <= fine
    for _d, _p, dw in bolts._METRIC_FINE_MORE:  # the coarse size's bearing
        assert any(s.bearing == pytest.approx(dw * MM) for s in bolts.sizes("iso_coarse"))
    hexbolt = fasteners.table("asme.hexBolt")
    for _label, d, n, f in bolts._INCH_LARGE:
        r = hexbolt.at(d * IN)
        assert r.values[0] == n and hexbolt.value(r, "acrossFlats", "nominal") == pytest.approx(
            f * IN
        )


KSI = 6.894757293168361e6


@pytest.mark.parametrize(
    ("key", "yield_ksi", "tensile_ksi"),
    [
        # Lacewing Lab's bolt_grades.dart: Portland Bolt's ASTM summaries, ASME II-D for B7M
        ("astm_f3125_144", 115, 144),
        ("astm_f1554_36", 36, 58),
        ("astm_f1554_55", 55, 75),
        ("astm_f1554_105", 105, 125),
        ("astm_a193_b7m", 80, 100),
        ("astm_a193_b8m_cl1", 30, 75),
        ("astm_a193_b8_cl2", 100, 125),
        ("astm_a320_l43", 105, 125),
        ("astm_f593_group1_cw1", 65, 100),
        ("astm_f593_group1_cw2", 45, 85),
        ("astm_f593_group2_cw1", 65, 100),
        ("astm_f593_group2_cw2", 45, 85),
    ],
)
def test_added_grades_are_the_specified_minimums(key, yield_ksi, tensile_ksi):
    from puzzle.materials import library_material

    m = library_material(key)
    assert m.properties["yield_strength"].to_si() == pytest.approx(yield_ksi * KSI, abs=0.6e6)
    assert m.properties["tensile_strength"].to_si() == pytest.approx(tensile_ksi * KSI, abs=0.6e6)
    assert "proof_strength" not in m.properties  # none of these specifies one


def test_the_agent_reaches_sizes_tables_and_vdi_2230(tmp_path):
    import asyncio

    pytest.importorskip("mcp")
    from puzzle.agent.mcp import build_server
    from puzzle.model.schema import Project
    from puzzle.project import save_project

    save_project(Project(name="p"), tmp_path / "p")
    server = build_server(tmp_path / "p", "test")

    def call(tool, **arguments):
        result = asyncio.run(server.call_tool(tool, arguments))
        assert not result.is_error, result
        return result.structured_content.get("result", result.structured_content)

    m64 = call("bolt_sizes", standard="iso_coarse", contains="M64")["sizes"]
    assert [s["name"] for s in m64] == ["M64"] and m64[0]["head"]["table"] == "iso.hexBolt"
    unc = call("bolt_sizes", standard="unc", contains="4-4", nut="heavy_hex")["sizes"]
    four = next(s for s in unc if s["name"] == "4-4 UNC")  # "#4-40 UNC" matches too
    assert four["nut"]["height_m"] == pytest.approx(
        fasteners.nut("heavy_hex", 4 * IN, metric=False).height
    )
    listed = call("fastener_tables")
    assert "iso.washerNormal" in {t["id"] for t in listed["tables"]}
    one = call("fastener_tables", table="iso.hexNut", size="M12")
    assert one["rows"][0]["values"]["m max"] == pytest.approx(10.8 * MM)
    # Example B1's bolt: δS 2.95e-6 mm/N, FMzul 64.8 kN at μG 0.10.
    b1 = call(
        "bolt_vdi2230",
        size="M12",
        clamp_length=0.042,
        grade="10.9",
        head="socket",
        tapped=True,
        shank_length=0.024,
        outer_diameter=0.080,
        mu_thread=0.10,
        mu_head=0.10,
        axial_load=24900,
        tightening_factor=1.8,
    )
    assert b1["resilience_m_per_N"]["bolt"] * 1e9 == pytest.approx(2.95, abs=0.005)
    assert b1["assembly_preload_N"] / 1e3 == pytest.approx(64.8, abs=0.15)
    assert b1["working"]["bolt_max_load_N"] > b1["assembly_preload_N"]
    with pytest.raises(Exception, match="no bolt size 'M11'"):  # a coded error for the agent
        asyncio.run(server.call_tool("bolt_vdi2230", {"size": "M11", "clamp_length": 0.04}))
