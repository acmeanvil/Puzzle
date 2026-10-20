"""Run the agent evaluation suite (puzzle.agent.evals) against one or more models.

    python scripts/agent_eval.py --provider openai --model qwen3:8b --model llama3.2:3b
    python scripts/agent_eval.py --provider anthropic --model claude-haiku-5-5
    python scripts/agent_eval.py --provider openai --base-url http://localhost:8080/v1 --model local

Prints a Markdown table (one row per model) and each failure's detail. Tasks that run
a solver are included when it is available here.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from puzzle.agent.evals import run_suite, table
from puzzle.agent.settings import AgentSettings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic"])
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parents[1] / "tests"))
    from solvers import available

    solvers = [s for s in ("calculix",) if available(s)]
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="puzzle-eval-"))
    rows = {}
    for model in args.model:
        provider = AgentSettings(
            provider=args.provider, model=model, base_url=args.base_url
        ).build()
        rows[model] = outcomes = run_suite(
            provider, workdir / model.replace(":", "-"), solvers=solvers
        )
        for o in outcomes:
            print(f"{model} / {o.task}: {'pass' if o.passed else 'FAIL'} ({o.detail})", flush=True)
    print()
    print(table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
