# 0068. Ship Puzzle as one installer per system, with every solver inside

- Status: Accepted (2026-11-05); steps 1 to 4 of 5 built (step 4 on 2026-11-10)
- Date: 2026-11-05

## Context

Today Puzzle is installed from a source checkout with pixi: one environment for Puzzle and
one per native solver (ADR 0013). The solvers conda-forge lacks run in container images
(ADR 0007), which the user builds locally. That suits a developer, not an engineer. The
maintainer's goal (2026-11-05): **one monolithic release per system, a fully working
Puzzle with every solver, installed with minimal interaction, that works out of the box.**
Nothing to configure and no compilers. Containers are fine as long as their setup is
invisible: the user never installs or configures a container engine or a VM.

What there is to package, measured on macOS Intel (2026-11-05):

| Environment | On disk | Packages |
| --- | --- | --- |
| Puzzle + GUI (the `dev` environment, test tools included) | 3.4 GB | 344 |
| FEniCSx (PETSc, MPI) | 2.7 GB | 271 |
| MMG (mmgpy) | 1.6 GB | 180 |
| CalculiX with PARDISO (MKL) | 1.4 GB | (build) |
| Scripts (numpy, scipy, polars) | 443 MB | 32 |
| Netgen | 390 MB | 58 |
| Job runner (PSI/J) | 89 MB | — |
| CalculiX (conda-forge) | 82 MB | 11 |
| Container images: Code_Aster 1.5 GB, OpenFOAM 1.0 GB, Code_Saturne 0.8 GB, Elmer 0.6 GB | | |

Across the environments, 921 package installs are only 506 distinct packages: a shared
package cache with hard links (what conda and constructor do) stores each once.

Where each solver comes from today:

| Solver | linux-64 | linux-aarch64 | osx-64 | osx-arm64 | win-64 |
| --- | --- | --- | --- | --- | --- |
| CalculiX | conda-forge | conda-forge | conda-forge | conda-forge | conda-forge |
| FEniCSx | conda-forge | conda-forge | conda-forge | conda-forge | conda-forge (no PETSc) |
| Code_Aster | conda-forge | container | container | container | conda-forge |
| Elmer | our recipe (unsubmitted) or container | container | our recipe or container | container | container |
| Code_Saturne | container | container | container | container | container |
| OpenFOAM | conda-forge | container | container | container | container |
| Netgen, MMG | conda-forge | conda-forge | conda-forge | Netgen only | conda-forge |
| py-slvs, manifold3d | PyPI wheels | PyPI | PyPI | PyPI | PyPI |

Other constraints:

- **Licences.** Puzzle is GPL-3.0-or-later. Most solvers are GPL or LGPL, and the
  libraries are BSD, MIT, LGPL or MPL. Two exceptions:
  - Intel MKL (used by our CalculiX PARDISO build, ADR 0051) is under Intel's Simplified
    Software License. Redistributing MKL itself is allowed, but distributing a GPL program
    linked to a proprietary library is not clearly allowed by CalculiX's GPL.
  - PSI/J pins `packaging<=25` (ADR 0067), which only matters for keeping it in its own
    environment.
- **Signing.** An unsigned macOS app is blocked by Gatekeeper, and an unsigned Windows
  installer warns. Notarizing needs every Mach-O file inside signed, conda packages
  included. Both need paid certificates: Apple Developer ID, and Windows code signing.
- **Reproducibility.** A study's run key and results name the solver versions (ADR 0011):
  a release must pin every package, as `pixi.lock` does now.

## Decision

1. **Puzzle and everything conda-forge lacks become conda packages, in Puzzle's own
   channel.** Recipes are built with rattler-build in CI on each platform and published to
   a public channel (prefix.dev, or GitHub releases as a channel). They cover:
   - `puzzle-fem` itself;
   - `py-slvs` and `manifold3d` (PyPI only today);
   - `elmerfem` (our recipe, until conda-forge accepts it);
   - native builds of the container-only solvers, platform by platform (decision 4).

   Each recipe that conda-forge would take goes there in time, and our copy is then
   dropped.
2. **One installer per system, made by conda constructor from the lock file.** A script
   (`scripts/make_installer.py`) writes `construct.yaml` from `pixi.lock`: every package
   by URL and hash, so an installer is exactly the lock. Puzzle is the base environment;
   each solver environment is an `extra_envs` entry (calculix, fenicsx, code-aster,
   netgen, mmg, scripts, runner, and those decision 4 adds), with the same names as
   pixi's, so `jobs.runtime.pixi_solver_dirs` finds them beside Puzzle as it does now.
   Outputs:
   - macOS: a signed, notarized `.pkg` for arm64 and one for x86-64 while Intel is
     supported;
   - Windows: a signed `.exe`;
   - Linux: a `.sh` (x86-64 and aarch64).

   Each installs per user by default (no administrator), adds a Start-menu, Dock or
   desktop entry, and registers `.puzzle` archives (ADR 0061). It asks nothing but where
   to install.
3. **Everything inside, nothing downloaded at first launch.** The installer holds every
   solver: those that run natively, and on macOS and Windows the Linux machine with the
   rest (decision 4). Size is the price: an estimated 2.5–3.5 GB download and 6–8 GB
   installed on Linux, about 1.5 GB more of each on macOS and Windows for the machine,
   with packages shared across environments. Engineering
   suites ship far larger. A small web installer (the same installer, fetching from the
   channel) can follow for those who want one.
4. **Every solver works on every system at install; how it runs is Puzzle's business.**
   - **Natively** where a conda package exists or we build one: all of them on Linux
     (Elmer and Code_Saturne build there as our container images already do), and
     CalculiX, FEniCSx, Netgen and MMG everywhere.
   - **In Puzzle's own Linux machine** for the rest on macOS and Windows: Code_Aster on
     macOS, Elmer, Code_Saturne and OpenFOAM. The installer ships a Linux root filesystem
     with those solvers installed natively in it, built from the same recipes as the
     container images (`containers/`). It sets it up silently:
     - on Windows, as a WSL2 distribution imported per user (`wsl --import puzzle …`);
     - on macOS, as a Lima VM under Apple's Virtualization framework (Lima is Apache-2.0,
       shipped inside the app, with user-mode networking, so no root).

     Puzzle reaches it through the job runner exactly as a remote host (ADR 0021, 0067):
     `wsl -d puzzle` or Lima's own ssh in place of ssh. The case is staged into the
     machine's own disk (fast, unlike files shared across the VM boundary), the runner
     there runs the solver, and the case comes home checked against its manifest. The user
     sees only the solver's name and "runs in Puzzle's Linux environment" on the Solvers
     page. No container engine, image pull or settings.
   - **Started on demand, stopped when idle.** The machine starts with the first solve
     that needs it (seconds) and stops after a quiet spell, so it costs no memory
     otherwise. Its memory and cores follow Settings > Solvers (cores per run), and it
     takes at most a set share of the machine's.
   - **One interaction may be unavoidable:** Windows without WSL turned on needs WSL
     enabled once. The installer asks for administrator rights with one prompt and, if
     Windows wants a restart, finishes the setup by itself after it. Everything else
     installs per user, without questions.
   - **Native ports are speed-ups, not prerequisites.** Building Code_Aster for macOS, or
     Elmer, Code_Saturne and OpenFOAM for macOS and Windows, removes the Linux machine's
     start-up time and copies for those solvers. Each moves out of the machine once its
     native build passes the same benchmarks; until then, the machine is how it works.
   - **Users who already run Docker or Podman** may still choose them (ADR 0013's
     runtimes, `PUZZLE_RUNTIME`). The release never needs them.
5. **Solvers compiled as needed, where licences decide.** Prebuilt binaries for everything
   whose licence allows redistribution. CalculiX with PARDISO is built on the user's
   machine (ADR 0051's script, with conda-forge's compilers and MKL). The GPL restricts
   distribution, not private builds, so this keeps the release clear of the MKL question.
   It runs once, in the background after installing, and asks nothing. The plain CalculiX
   (SPOOLES) works until it is done. If the build fails, Puzzle says so and keeps the
   plain one.
6. **A Solvers page shows what is there.** Settings > Solvers lists each solver: native
   (its version), in Puzzle's Linux environment, being built, or not available on this
   system with the reason. It also offers Repair, which reinstalls a solver environment
   from the installer's own packages. There is nothing to choose for a working system.
7. **Updates are new releases, side by side.** A release is a versioned folder; the
   launcher runs the newest, and an older one is kept until the user removes it, so
   earlier projects can rerun their exact solver versions. Puzzle checks a release feed
   and offers the new installer; it never updates itself in place.
8. **The same environments on remote hosts.** A remote host (ADR 0021) gets the release's
   solver environments with pixi from the same channel and lock, so local and remote runs
   use identical solvers. This is an action in Remote hosts…, later.
9. **Licences shipped and shown.** Every conda package carries its licence files. The
   installer collects them into `licenses/`, Help > Licences lists every component with
   its licence and source (feedstock or recipe), and the release notes carry the GPL's
   source offer (this repository and the recipes).

## Consequences

- An engineer installs one file and has every solver: no pixi, Docker, compilers or
  configuration.
- Every solver works from the first release on every system: the Linux machine covers
  what isn't native, with no setup by the user. Native ports can follow solver by solver,
  as speed-ups.
- The Linux machine is ours to maintain: a root filesystem per release (about 3–4 GB
  with its solvers), its start, stop and resource limits, and a WSL and a Lima path.
  Both reuse the remote-host path of ADR 0021 and 0067, so the runner, staging and checked
  fetch are not new code.
- A solve in the machine pays a start-up (a few seconds when stopped) and a copy of the
  case each way. The copies are local disk to local disk, and far faster than files
  shared across the VM boundary.
- CI grows: package builds per platform, installers, signing and notarization, and a smoke
  test of each installer (install silently, run the cantilever on every solver, uninstall).
- Releases are large. Disk and download sizes go in the release notes.
- Paid certificates are a running cost: Apple Developer ID, and Windows code signing.

## Alternatives considered

- **PyInstaller, Briefcase or Nuitka (a frozen Python app).** Freezing Qt, VTK, OCCT and
  Gmsh is fragile, and it can't hold the solvers' separate environments; each solver would
  be a hand-made bundle. Rejected.
- **pixi-pack (an environment packed into a self-extracting file).** It is good for one
  environment and supports PyPI wheels. But it makes no native installer, shortcut or file
  association, keeps no shared package cache between environments, and isn't notarizable
  as it is. Kept for remote hosts and offline solver bundles, not for the desktop.
- **Everything in containers, the GUI included.** It needs X or VNC for the GUI, which ADR
  0013 already rejected.
- **Containers through Docker or Podman for the gaps.** That means an engine for the user
  to install and run, image pulls, and slow bind mounts on macOS and Windows. A Linux
  machine of Puzzle's own, with the solvers installed in it, gives the same result with
  nothing for the user to do. Bundling Podman and its VM (as Podman Desktop does) is the
  fallback if Lima can't be shipped inside a notarized app.
- **Flatpak or Snap.** Their sandboxes block what Puzzle does: start solver processes,
  ssh, container engines, process groups. AppImage may follow for Linux users who want
  one file.
- **A thin installer that downloads solvers on first launch.** Smaller, but not monolithic,
  and it fails offline. It is kept as the optional web installer only.

## Built so far

**Step 1 (2026-11-05): Puzzle's own conda packages and the channel.**

- `packaging/recipes/` (rattler-build):
  - `puzzle-fem` (noarch, its run requirements exactly pixi.toml's default and gui
    features, checked by `tests/test_packaging.py`);
  - `py-slvs` (PyPI sdist, SWIG);
  - `manifold3d` (PyPI sdist, nanobind, TBB; Clipper2 fetched at manifold's pinned
    commit).

  Each runs its tests as it builds: imports, a solved sketch system, a cube's volume,
  `puzzle --help`. `scripts/build_packages.py` builds them in order into a local channel
  (`pixi run -e packaging build-packages`), printing `[progress] i/n` lines for watchers.
- Fixes the builds needed, found on macOS Intel:
  - SolveSpace's CMakeLists needs `CMAKE_POLICY_VERSION_MINIMUM=3.5` under CMake 4;
  - scikit-build-core's 5 s CMake probe timed out on macOS's first runs of a fresh
    binary, so the recipe sets `CI` (20 s) and runs CMake once first;
  - Clipper2 at manifold's pin misses `<iterator>`;
  - macOS on Intel targets 11.0: current libc++ refuses older, and Qt 6.11 needs 12.
- The channel is `https://github.com/acmeanvil/Puzzle/releases/download`: one release per
  platform, tagged with its name. `scripts/publish_channel.py` adds packages and
  re-indexes with rattler-index; it never replaces a published package, since lock files
  pin its hash. Puzzle's build number is CI's run number.
- `.github/workflows/packages.yml` builds on linux-64, linux-aarch64, osx-64, osx-arm64
  and win-64 when the recipes change, and publishes on a published release or a manual run
  with "publish" ticked. First published 2026-11-06 (puzzle-fem 0.0.1, py-slvs 1.0.6,
  manifold3d 3.5.4 on all five platforms); `pixi add --channel <channel> puzzle-fem`
  installs a working Puzzle.

**Step 2 (2026-11-06): the installer from the lock file, and its smoke test.**

- `scripts/make_installer.py` (`pixi run -e packaging make-installer [--build]`) writes
  `construct.yaml` with explicit package lists (`@EXPLICIT`, URL and md5) from
  `pixi.lock`, so nothing is solved and the installer is exactly the lock:
  - the base is pixi's new `app` environment: Puzzle and its GUI in the `main` solve
    group (the same versions as `dev`), plus conda itself. constructor refuses extra
    environments without conda in the base, and the Solvers page's Repair (decision 6)
    needs it anyway;
  - the lock's PyPI entries become Puzzle's conda packages, a local build first
    (`--packages`) and then the channel. A dependency of theirs that the lock lacks is
    refused;
  - each solver environment (calculix, fenicsx, code-aster, netgen, mmg, scripts,
    runner) is an extra environment with pixi's name. The lock decides per platform:
    there is no Code_Aster on macOS until the Linux machine (decision 4);
  - per user, no conda init, no registered Python, and the lock's oldest systems as
    `virtual_specs`.
- The installed layout is `<prefix>/envs/<name>`. `jobs.runtime.environment(name)` finds
  an environment there or as a pixi sibling, and every lookup uses it: solvers, Netgen,
  MMG, scripts, the runner.
- puzzle-fem carries a menuinst entry (`packaging/recipes/puzzle-fem/menu.json`, icons
  including a new `Puzzle.ico`): Start menu and desktop on Windows, where `.puzzle`
  archives open in Puzzle; an app on macOS; a desktop entry on Linux.
- `scripts/smoke_installer.py` installs in batch mode into a temporary folder (no
  shortcuts unless asked). It runs `puzzle --help` and the imports, solves the
  cantilever's Tip load with the installed CalculiX with nothing on PATH, and checks
  dolfinx, Netgen, MMG, the scripts and runner Pythons.
- **Measured, macOS Intel `.sh`:** the installer is 1.09 GB, and the smoke test passes
  (Tip load: 0.001522 m, solved natively in 2.7 s). Installing takes about 13 minutes on
  this Mac, mostly extracting seven environments. Step 3 should look at that (the `.pkg`,
  a faster extraction, fewer duplicate files).
- `tests/test_packaging.py`: the explicit lists match the lock package for package, the
  extra environments, Code_Aster left out where the lock has none, and a missing
  dependency refused.

**Step 3 (2026-11-07 to 11-08):** installers for every platform built and smoke-tested in
CI (install, CalculiX and FEniCSx solves, every solver environment, ParaView), one
platform or a list on demand; signing and notarization ready to switch on.

**Step 4 (2026-11-10): CalculiX with PARDISO built at home, and the Solvers page
(decisions 5 and 6).**

- The installer ships the `calculix-pardiso` environment (compilers, MKL, ARPACK,
  SPOOLES; never CalculiX linked with MKL) on linux-64, osx-64 and win-64; the lock has
  none for Apple silicon (no MKL), so it is left out there.
- `puzzle.solver_builds` builds it (ADR 0051's build, moved from a shell script to Python
  so that the installed app needs no bash or make): CalculiX's source fetched and checked
  by its SHA-256, the files Makefile.inc lists compiled in parallel, archived and linked,
  then a one-element deck solved with the new program must say PARDISO before it is put
  in place (the old one renamed aside, since Windows won't overwrite a running program).
  Its state is kept in `<user data dir>/solver-builds/`: ready, building, failed (with the
  reason), built. On Windows it builds with conda-forge's MinGW gcc and gfortran 16.
- The GUI starts it once, 8 s after opening, with a toast, and says when it ends (built,
  or failed with the reason; the plain CalculiX is used meanwhile and after a failure).
  A failed build (offline, say) is tried again a day later. Preference
  `solvers.build_pardiso` (Settings > Solvers, on by default).
- Decision 3 said nothing is downloaded at first launch: the CalculiX source is (7 MB,
  from dhondt.de) when the build runs, since shipping it in a package is more machinery
  for no gain; without a network the plain CalculiX keeps working and the build waits.
- The Solvers page: Settings > Solvers lists each solver and how a solve runs it here
  (native with its program, in a container with its image, or not available with why),
  CalculiX's note (PARDISO, or SPOOLES and PARDISO's state), and Build CalculiX with
  PARDISO now. `puzzle solvers [--build] [--json]` says the same. Repair (reinstalling an
  environment from the installer's packages) is not built yet.
- Measured on Windows 11: the Python build compiles CalculiX's 1170 files with MinGW gcc and
  gfortran 16, links against mkl_rt, passes its PARDISO check and installs; the CalculiX
  benchmarks pass on it. Found on the way: no shell runs the compiler, so ARCH=Linux takes
  no quotes (CalculiX.h compares it to pick FORTRAN()'s name mangling), and 2.23's
  Makefile.inc lists mafillmm as .c beside its .f with only the .f there.

## Decided (2026-11-05, maintainer)

- **Signing:** certificates are in the works (an Apple Developer ID and Windows code
  signing). The CI signing steps are built so that they can be switched on when the
  certificates arrive; until then, test builds are unsigned.
- **Channel:** GitHub. Puzzle's conda packages are published as GitHub release assets, as
  a channel (`repodata.json` per platform), beside the installers.
- **MKL:** CalculiX with PARDISO is built on the user's machine (decision 5). The release
  ships conda-forge's CalculiX (SPOOLES) and MKL itself, never the two linked together.
- **Intel Macs:** the first release has installers for both arm64 and x86-64 macOS. Intel
  support is evaluated again after it.
- **The Linux machine's memory:** half of the computer's memory by default, with no upper
  limit. A preference (Settings > Solvers, "Memory for Puzzle's Linux environment") lets
  the user give it more or less; it applies the next time the machine starts.

- **macOS 14 or later (2026-11-07):** ParaView 6.2 (ADR 0070) needs a Qt that
  conda-forge builds for macOS 14+ only, so Puzzle's installers require macOS 14
  (Sonoma); Macs that can't run it (Intel models from before about 2018) are left out.

## Open questions

- Lima inside a notarized app (to be decided after further investigation): whether
  `limactl` and its VZ driver can be signed with Puzzle's identity and the
  `com.apple.security.virtualization` entitlement. If not, the fallback is Podman's
  machine, or Apple's `container` framework on macOS 26.
- How long the Linux machine stays up when idle before it stops.
- The order of the native ports in decision 4. They are speed-ups, so they can wait until
  the installers work.
