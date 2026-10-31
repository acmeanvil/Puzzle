"""Skills for agents (ADR 0057): what each finished feature asks of an agent, as a short
how-to a model reads when the task needs it.

A skill is a Markdown file here: a header of `name`, `description` (one line: when to
read it) and `adr` (the decision it covers), then the steps, the tools and their
arguments, units and the usual mistakes. The built-in agent's prompt lists each
skill's name and description only, so a small local model's context holds the
catalogue, not the bodies; `skills_list` and `skill_read` (MCP) give the same to
outside agents. Every finished ADR that changes what an agent can do gets one
(`tests/test_agent_skills.py` checks the files and that they name real tools).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib.resources import files


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    adr: str
    body: str


def _parse(text: str) -> Skill:
    if not text.startswith("---\n"):
        raise ValueError("a skill starts with a --- header")
    head, _, body = text[4:].partition("\n---\n")
    fields = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    missing = [k for k in ("name", "description", "adr") if not fields.get(k)]
    if missing:
        raise ValueError(f"a skill's header needs {', '.join(missing)}")
    return Skill(fields["name"], fields["description"], fields["adr"], body.strip() + "\n")


@cache
def skills() -> dict[str, Skill]:
    out = {}
    for item in sorted(files(__package__).iterdir(), key=lambda p: p.name):
        if item.name.endswith(".md"):
            skill = _parse(item.read_text())
            out[skill.name] = skill
    return out


def catalog() -> str:
    """One line per skill, for a prompt: `name: when to read it`."""
    return "\n".join(f"- {s.name}: {s.description}" for s in skills().values())


def read(name: str) -> str:
    found = skills().get(name)
    if found is None:
        raise KeyError(f"no skill '{name}'; the skills are: {', '.join(skills())}")
    return f"# {found.name} (ADR {found.adr})\n\n{found.body}"
