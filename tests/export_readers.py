"""Readers for the export tests (ADR 0043): Exodus through VTK's Exodus reader, and the
universal file's datasets parsed from their record layouts (fixed-width fields)."""

from __future__ import annotations


def read_exodus(path, step: int):
    import pyvista as pv
    from vtkmodules.vtkIOExodus import vtkExodusIIReader

    r = vtkExodusIIReader()
    r.SetFileName(str(path))
    r.UpdateInformation()
    r.ApplyDisplacementsOff()
    r.GenerateGlobalNodeIdArrayOn()
    r.GenerateGlobalElementIdArrayOn()
    for kind in (r.NODAL, r.ELEM_BLOCK, r.GLOBAL):
        r.SetAllArrayStatus(kind, 1)
    for kind in (r.NODE_SET, r.SIDE_SET):
        for i in range(r.GetNumberOfObjects(kind)):
            r.SetObjectStatus(kind, i, 1)
    r.SetTimeStep(step)
    r.Update()
    out = pv.wrap(r.GetOutput())
    times = r.GetOutputInformation(0).Get(r.GetExecutive().TIME_STEPS())
    found = {}
    for k in range(out.n_blocks):
        group = out[k]
        for j in range(group.n_blocks if group is not None else 0):
            found[(out.get_block_name(k), group.get_block_name(j))] = group[j]
    return found, times


def read_unv(path) -> dict[int, list[list[str]]]:
    """The datasets of a universal file: number -> each occurrence's lines (between the
    `    -1` delimiters, the number line left out)."""
    lines = path.read_text().splitlines()
    out: dict[int, list[list[str]]] = {}
    i = 0
    while i < len(lines):
        assert lines[i] == "    -1", f"line {i + 1}: expected a delimiter"
        number = int(lines[i + 1][:6])
        end = lines.index("    -1", i + 2)
        out.setdefault(number, []).append(lines[i + 2 : end])
        i = end + 1
    return out


def fixed_ints(line: str) -> list[int]:
    assert len(line) % 10 == 0, line
    return [int(line[k : k + 10]) for k in range(0, len(line), 10)]


def fixed_floats(line: str, width: int) -> list[float]:
    assert len(line) % width == 0, line
    return [float(line[k : k + width].replace("D", "E")) for k in range(0, len(line), width)]


def read_mesh(sets):
    (nodes,) = sets[2411]
    coords = {}
    for k in range(0, len(nodes), 2):
        label, cs1, cs2, _ = fixed_ints(nodes[k])
        assert len(nodes[k]) == 40 and len(nodes[k + 1]) == 75 and (cs1, cs2) == (1, 1)
        coords[label] = fixed_floats(nodes[k + 1], 25)
    (elems,) = sets[2412]
    elements, k = {}, 0
    while k < len(elems):
        label, code, phys, mat, _, count = fixed_ints(elems[k])
        k += 1
        labels = []
        while len(labels) < count:
            labels += fixed_ints(elems[k])
            k += 1
        elements[label] = (code, labels)
    return coords, elements


def read_2414(sets):
    out = []
    for lines in sets[2414]:
        head = {
            "label": fixed_ints(lines[0])[0],
            "name": lines[1],
            "location": fixed_ints(lines[2])[0],
            "ids": lines[3:8],
            "record9": fixed_ints(lines[8]),
            "record10": fixed_ints(lines[9]),
            "record12": fixed_floats(lines[11], 13) + fixed_floats(lines[12], 13),
        }
        values, k = {}, 13
        while k < len(lines):
            label = fixed_ints(lines[k])[0]
            k += 1
            row = []
            while len(row) < head["record9"][5]:
                row += fixed_floats(lines[k], 13)
                k += 1
            values[label] = row
        head["values"] = values
        out.append(head)
    return out
