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
    from puzzle.model.units import parse_quantity
    from puzzle.settings import length_unit

    if not comp.contacts:
        window.statusBar().showMessage(
            "A thread is set on a contact between two cylinders: add the contact first "
            "(Find contacts… or Add contact…)", 10000)  # fmt: skip
        return None
    names = [c.name for c in comp.contacts]
    first = comp.contacts[index] if index is not None else comp.contacts[0]
    by_name = {c.name: c for c in comp.contacts}
    hints: dict = {}
    unit = length_unit()[0]

    def chosen(v):
        """The contact the form is about: its fields start from its thread (PANEL-22)."""
        return by_name.get(v.get("contact"), first)

    def old_of(v):
        return chosen(v).thread

    def hint_of(v):
        c = chosen(v)
        if c.thread is None and c.name not in hints:
            hints[c.name] = _suggested(window, comp, c)
        return hints.get(c.name) if c.thread is None else None

    def size_of(v):
        old = old_of(v)
        return old.size if old is not None else (hint_of(v) or {}).get("size") or "M16"

    def keep(attr, blank, shown=lambda x: x):
        """A field's default: the chosen contact's thread's, else `blank`."""
        return lambda v: shown(getattr(old_of(v), attr)) if old_of(v) is not None else blank

    def sizes(v):
        found = ts.sizes_for(v.get("form", "iso"))
        return [(n, n) for n in found] or [("(none)", None)]

    def quantity(text: str):  # a bare number in the unit shown, a length (PANEL-21)
        value, given = parse_quantity(text, unit, "length")
        return {"value": value, "unit": given}

    def mm(q) -> str:  # as stored, every digit: an untouched field writes it back as it was
        return f"{q.value:.15g} {q.unit}" if q is not None else ""

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
        Ask("form", "Form", choices=ts.form_choices(), follow=keep("form", "iso")),
        Ask("size", "Size", choices=sizes, follow=size_of,
            shown=lambda v: v.get("form") in ts.SIZED,
            help="Suggested from the cylinders when they have no thread yet"),
        Ask("major", f"Major diameter ({unit})", kind="text", follow=keep("major", "", mm),
            shown=lambda v: v.get("form") not in ts.SIZED, help="A bare number is in "
            f"{unit}; or with its unit: 20 mm"),
        Ask("pitch", f"Pitch ({unit})", kind="text", follow=keep("pitch", "", mm),
            shown=lambda v: v.get("form") not in ts.SIZED, help="A bare number is in "
            f"{unit}; or with its unit: 4 mm"),
        Ask("angles", "Flank angles (degrees)", kind="text", default="30, 30",
            shown=lambda v: v.get("form") == "custom",
            help="The load flank's, then the other's, from the radial plane"),
        Ask("starts", "Starts", kind="text", follow=keep("starts", "1", str)),
        Ask("hand", "Hand", choices=[("Right", "right"), ("Left", "left")],
            follow=keep("hand", "right")),
        Ask("type", "Flanks", choices=kinds,
            follow=lambda v: chosen(v).type if chosen(v).type in ts.TYPES else "bonded"),
        Ask("bearing", "Bearing flank", choices=[(t, k) for k, t in ts.BEARING_NAMES.items()],
            follow=keep("bearing", "auto")),
        Ask("compliance", "Teeth's give",
            choices=[(t, k) for k, t in ts.COMPLIANCE_NAMES.items()],
            follow=lambda v: (old_of(v).compliance if old_of(v) is not None
                              and isinstance(old_of(v).compliance, str) else "teeth")),
        Ask("clearance", f"Clearance ({unit})", kind="text", follow=keep("clearance", "", mm),
            shown=lambda v: v.get("type") == "frictionless",
            help=f"Backlash along the flanks; a bare number is in {unit}; empty: none"),
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
