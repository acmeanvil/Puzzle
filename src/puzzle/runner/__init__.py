"""The job runner (ADR 0067): a small program that owns solves, here and on remote hosts.

Puzzle hands a solve to a runner instead of running it itself: the case folder, the
command (or a shell script for a remote host), the resources and the scheduler. The
runner starts it (at once, or through Slurm, PBS or LSF via PSI/J), keeps its state on
disk, and answers questions about it. It is a daemon detached from whoever submitted the
job, so a solve outlives Puzzle, an SSH session and a sleeping laptop.

One runner per user and protocol version on each machine, in its own folder
(`<dir>/p<PROTOCOL>/`): `runner.json` (its pid and socket), `jobs/<id>.json`, `psij/`.
Clients talk JSON lines to its Unix socket; `connect` bridges stdin and stdout to it,
which is how Puzzle reaches a remote runner over `ssh`.

This package imports only the standard library (and PSI/J, lazily, for schedulers): it
is copied to remote hosts as one zipapp (`build.py`) and must run there without Puzzle.
Python 3.10 or later.
"""

PROTOCOL = 1  # bumped when requests or answers change incompatibly
VERSION = "1.0"

#: The states a job goes through; the last five are final.
STATES = ("queued", "running", "completed", "failed", "cancelled", "walltime_exceeded",
          "out_of_memory", "job_lost")  # fmt: skip
FINAL = frozenset(STATES[2:])
