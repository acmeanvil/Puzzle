"""Where things live inside a project directory."""

PROJECT_FILE = "project.toml"
HISTORY = "history.jsonl"  # the command log (ADR 0015); part of the project's record
RESULTS_DIR = "results"
WORK_DIR = ".puzzle/runs"  # solver working files; disposable
LOCK = ".puzzle/lock"
JOURNAL = ".puzzle/journal.json"  # a change being written: finished on the next open
TORN = ".puzzle/history.torn"  # torn history lines set aside, never dropped
