---
name: remote-runs
description: run a study on another machine over SSH, at once or through Slurm or PBS; hosts, resources, cancelling, the coded failures, and what it costs an agent's budget
adr: 0021
---
Read this when the user wants a study run on a lab machine or a cluster, when a model
is too big for this computer, or when a run failed with `host_unreachable`,
`queue_rejected`, `walltime_exceeded` or `transfer_failed`.

Hosts are the user's, kept in `<user data dir>/hosts.toml`, never in a project. You
can list and check them (`hosts_list`, `host_check`) but not add or change them: that
is the user's job (`puzzle hosts add`, or Project → Remote hosts… in the GUI), because
a host is their account on their machine. SSH takes credentials from the user's agent
and `~/.ssh/config`, never from Puzzle.

A remote run is the same as a local one. The case is written here, copied to the
host's `work_dir`, solved there (natively from the pixi environments in `envs_dir`, or
in Docker, Podman or Apptainer with Puzzle's images), streamed back line by line,
fetched whole, and read here. The run key does not depend on the host, so a result
computed on a cluster is reused locally and the other way round.

## Steps

1. `hosts_list` gives the names, what each runs (runtime, scheduler) and its cores.
   `host_check(name)` reaches the host and says whether it answers and which solvers it
   has. Check before the first run on a host.
2. Point the study at the host with one Edit:
   `{"command": "edit", "object": "<study>", "ops": [{"op": "set", "path": "remote",
   "value": {"host": "cluster", "processes": 4, "walltime": "02:00:00"}}]}`.
   `RemoteRun` fields: `host`, `processes` (MPI ranks, where the solver splits),
   `threads` (per process; 0 shares the host's cores out), `memory_gb`, `walltime`
   (HH:MM:SS, schedulers only), `partition`, `account`. Unset ones take the host's
   defaults. To run it here again, set `remote` to null.
3. Run it as usual with `submit` `{"command": "run_study", "study": "<name>"}`. You can
   also pass `"host": "<name>"` on the command to run it there once without editing the
   study.

## Units and limits

- `walltime` is a text `HH:MM:SS`. `memory_gb` is in gigabytes (a host setting, not SI).
- A remote run counts against an agent's budget at "act_in_budget" in core-hours as
  well as runs and seconds: cores × hours, from the host's cores (or processes ×
  threads). When `budget.core_hours` is spent, propose the run instead.
- With a scheduler, the run waits in the queue first. The log says "submitted job N";
  the job id is kept in the run's folder (`puzzle-job.id`).

## When it fails

- `host_unreachable`: ssh could not connect (wrong address, no key loaded, VPN off).
  Ask the user; never ask for a password.
- `queue_rejected`: the scheduler refused the job script (a wrong partition or
  account, too many cores). The message carries the scheduler's words. Fix `remote`.
- `walltime_exceeded`: the job ran out of time. Ask for a longer `walltime`, or make the
  model smaller.
- `transfer_failed`: the copy there or back failed (disk quota, permissions).
- `solver_failed` with a log: the solver itself failed, as it would here.

## Mistakes to avoid

- Don't put host names, addresses or users in a project's parameters or notes: hosts
  belong to the user.
- Don't set `processes` above what the solver can split. CalculiX uses threads only;
  give it `threads`.
- A cancel stops the remote run too (its process group, or `scancel`/`qdel`). Cancel
  through the GUI or the run's cancel, not by deleting files.
