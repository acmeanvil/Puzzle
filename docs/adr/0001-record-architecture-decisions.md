# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-10-17

## Context

Puzzle integrates many third-party tools (geometry kernel, meshers, eight or more solvers, a results store, ParaView). Choices made early constrain contributors for years, and the reasoning is easily lost.

## Decision

Record each significant decision as a short Markdown ADR in `docs/adr/`, using [template.md](template.md). Records are numbered, immutable once accepted, and superseded rather than edited when a decision changes.

## Consequences

- New contributors can see why the stack looks the way it does.
- Changing a major decision needs a new ADR, which forces the trade-offs to be written down.

## Alternatives considered

- Wiki pages: drift from the code and aren't reviewed in pull requests.
- Design doc only: good for the overview, poor at tracking individual decisions over time.
