"""Making the agent's abilities findable (ADR 0090 package 12, UXA-14).

`suggestions` gives three requests for an empty chat from the project's state (the
Advisor's next needed step, the results there are, a check before running). `slash`
reads "/bolts add M12 bolts" as a task in a skill's area: the skill is read first, so
its tools are loaded too. `skill_choices` and `object_choices` are what typing "/" and
"@" in the message box offer.
"""

from __future__ import annotations


def suggestions(project, runs=(), limit: int = 3) -> list[str]:
    from puzzle.advisor import advise, next_step
    from puzzle.model.schema import Geometry, Study

    out = []
    try:
        first = next_step(advise(project, runs))
    except Exception:  # noqa: BLE001 - advice is a help here, never a failure
        first = None
    if first is not None:
        out.append(f"Help me with the next step: {first.title.lower()}")
    studies = project.of_type(Study)
    solved = {r.study_name for r in runs}
    done = [s.name for s in studies if s.name in solved]
    if done:
        out.append(f"Summarise the results of {done[0]}: the largest values and where")
    if studies:
        out.append(f"Check {studies[0].name} for problems before I run it")
    elif project.of_type(Geometry):
        geometry = project.of_type(Geometry)[0].name
        out.append(f"Set up a static analysis of {geometry} in structural steel")
    else:
        out.append("What can Puzzle do, and how do I start?")
    out.append("Explain what this model does")
    return list(dict.fromkeys(out))[:limit]


def skill_choices() -> list[tuple[str, str]]:
    """(name, when to use it) for every skill, for the "/" list."""
    from puzzle.agent.skills import skills

    return [(s.name, s.description) for s in skills().values()]


def object_choices(project) -> list[str]:
    """Every object's name, for the "@" list."""
    return sorted({o.name for o in project.objects.values() if o.name}, key=str.lower)


def slash(text: str) -> tuple[str, str | None]:
    """A message starting "/<skill>" as the task with that skill read first, and the
    skill's name; any other message as it is."""
    from puzzle.agent.skills import skills

    if not text.startswith("/"):
        return text, None
    name, _, rest = text[1:].partition(" ")
    if name not in skills():
        return text, None
    task = rest.strip() or f"Help me with {name.replace('-', ' ')}."
    return f"Read the skill `{name}` with skill_read first, then: {task}", name
