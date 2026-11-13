"""Make it a thread… and Remove the thread (ADR 0076): a contact between two smooth
coaxial cylinders acting along the flanks. One form asks everything, one Edit makes it
(`thread_setup.thread_command`, as MCP `contact_thread`), so one undo takes it back."""

from __future__ import annotations

from puzzle import thread_setup as ts


def _suggested(window, comp, contact) -> dict | None:
    """The likely ISO size of the contact's cylinders, when the geometry says (OCC)."""
    try:
        from puzzle.geometry.contacts import contact_cylinders

        found = contact_cylinders(window.session.project, comp, contact, window.session.root)
    except Exception:  # noqa: BLE001 - a suggestion only: the form works without one
        return None
    if found is None:
        return None
    return ts.suggest_size(2 * min(found["source_radius"], found["target_radius"]))


def thread_contact(window, comp, index: int | None = None):
    """Ask a thread's form, size and flanks for a contact (asked too when `index` is
    None: the menu bar's item), then one Edit."""
    from puzzle.gui.quick_form import Ask
    from puzzle.model.units import known, plain

    if not comp.contacts:
        window.statusBar().showMessage(
            "A thread is set on a contact between two cylinders: add the contact first "
            "(Find contacts… or Add contact…)", 10000)  # fmt: skip
        return None
    names = [c.name for c in comp.contacts]
    first = comp.contacts[index] if index is not None else comp.contacts[0]
    old = first.thread
    hint = _suggested(window, comp, first) if old is None else None
    size = old.size if old is not None else (hint or {}).get("size") or "M16"

    def sizes(v):
        found = ts.sizes_for(v.get("form", "iso"))
        return [(n, n) for n in found] or [("(none)", None)]

    def quantity(text: str):
        number, _, unit = text.strip().partition(" ")
        unit = plain(unit.strip() or "mm")
        if not known(unit):
            raise ValueError(f"unknown unit '{unit}'")
        return {"value": float(number), "unit": unit}

    def mm(q) -> str:
        return f"{q.value:g} {q.unit}" if q is not None else ""

    def values(v) -> dict:
        form = v["form"]
        out = {"form": form, "starts": int(v.get("starts") or 1), "hand": v["hand"],
               "bearing": v["bearing"], "compliance": v["compliance"]}  # fmt: skip
        if form in ts.SIZED:
            out["size"] = v.get("size")
        else:
            out["major"] = quantity(v.get("major", ""))
            out["pitch"] = quantity(v.get("pitch", ""))
        if form == "custom":
            a, b = (float(x) for x in v.get("angles", "").replace(",", " ").split())
            out["flank_angles"] = (a, b)
        if v.get("clearance", "").strip():
            out["clearance"] = quantity(v["clearance"])
        return out

    def check(v):
        try:
            ts.make_thread(**values(v))
        except (ValueError, TypeError) as exc:
            return f"Check the numbers: {exc}"
        except Exception as exc:  # noqa: BLE001 - a PuzzleError's message, shown
            return getattr(exc, "message", str(exc))
        return None

    kinds = [(ts.TYPE_NAMES[k], k) for k in ts.TYPES]
    asks = []
    if index is None:
        asks.append(Ask("contact", "Contact", choices=[(n, n) for n in names]))
    asks += [
        Ask("form", "Form", choices=ts.form_choices(), default=old.form if old else "iso"),
        Ask("size", "Size", choices=sizes, default=size,
            shown=lambda v: v.get("form") in ts.SIZED,
            help=f"Suggested from the cylinders: {hint['size']}" if hint else ""),
        Ask("major", "Major diameter", kind="text", default=mm(old.major) if old else "",
            shown=lambda v: v.get("form") not in ts.SIZED, help="With its unit: 20 mm"),
        Ask("pitch", "Pitch", kind="text", default=mm(old.pitch) if old else "",
            shown=lambda v: v.get("form") not in ts.SIZED, help="With its unit: 4 mm"),
        Ask("angles", "Flank angles (degrees)", kind="text", default="30, 30",
            shown=lambda v: v.get("form") == "custom",
            help="The load flank's, then the other's, from the radial plane"),
        Ask("starts", "Starts", kind="text", default=str(old.starts if old else 1)),
        Ask("hand", "Hand", choices=[("Right", "right"), ("Left", "left")],
            default=old.hand if old else "right"),
        Ask("type", "Flanks", choices=kinds,
            default=first.type if first.type in ts.TYPES else "bonded"),
        Ask("bearing", "Bearing flank", choices=[(t, k) for k, t in ts.BEARING_NAMES.items()],
            default=old.bearing if old else "auto"),
        Ask("compliance", "Teeth's give",
            choices=[(t, k) for k, t in ts.COMPLIANCE_NAMES.items()],
            default=old.compliance if old and isinstance(old.compliance, str) else "teeth"),
        Ask("clearance", "Clearance", kind="text", default=mm(old.clearance) if old else "",
            shown=lambda v: v.get("type") == "frictionless",
            help="Backlash along the flanks, with its unit; empty: none"),
    ]  # fmt: skip
    got = window.ask_form(
        "Thread",
        asks,
        check=check,
        intro="The two cylinders stay smooth; the contact acts along the thread's flanks, "
        "giving each thread's load and the nut's wedge. Model both cylinders at one radius "
        "(best the screw's minor, d3/2). Thread-root peak stresses aren't represented.",
    )
    if got is None:
        return None
    contact = got.get("contact", first.name)
    thread = ts.make_thread(**values(got))
    return window.submit(ts.thread_command(comp, contact, thread, got["type"]))


def remove_thread(window, comp, index: int):
    """The contact back to a plain one of its type."""
    return window.submit(ts.thread_command(comp, comp.contacts[index].name, None))
