# 0068. Ship Puzzle as one installer per system, with every solver inside

- Status: Proposed
- Date: 2026-11-05

## Context

Today Puzzle is installed from a source checkout with pixi: one environment for Puzzle and
one per native solver (ADR 0013). The solvers conda-forge lacks run in container images
(ADR 0007), which the user builds locally. That suits a developer, not an engineer. The
maintainer's goal (2026-11-05): **one monolithic release per system, a fully working
Puzzle with every solver, installed with minimal interaction.** Nothing to configure, no
container engine to install, no compilers.

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

## Decision (proposed)

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
   solver the platform can run natively. Size is the price: an estimated 2.5–3.5 GB
   download and 6–8 GB installed, with packages shared across environments. Engineering
   suites ship far larger. A small web installer (the same installer, fetching from the
   channel) can follow for those who want one.
4. **Native solvers everywhere we can build them; containers only where we can't.**
   Containers need an engine the user installs, a VM on macOS and Windows, and slow file
   sharing, which is the opposite of a monolithic install. So we build the missing solvers
   natively, in this order of value and effort:
   - Elmer on every platform (our recipe; Windows with MSYS2-style compilers or
     conda-forge's MinGW);
   - Code_Aster on macOS (arm64 first) and linux-aarch64, from source with conda-forge's
     toolchain (its Linux recipe as the start; MED, MUMPS, PETSc and Scotch are on
     conda-forge);
   - Code_Saturne on Linux and macOS (autotools; Python front end; MED and CGNS from
     conda-forge);
   - OpenFOAM on macOS and linux-aarch64. On Windows, ESI's own MinGW builds are a
     candidate: check their licence and packaging.

   What can't be built yet runs in a Linux environment the installer carries:
   - on Windows, a WSL2 distribution imported by the installer (`wsl --import`; this
     needs WSL enabled once, with one prompt);
   - on macOS, a small VM under Apple's Virtualization framework (`container`, or Lima),
     with the same images.

   Each such solver is listed as "runs in Puzzle's Linux environment", and is dropped
   from the installer once a native build passes its benchmarks.
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
- Building the missing solvers natively is the large cost: Code_Aster on macOS and
  Code_Saturne are real porting work, each with its own benchmarks to pass before the
  installer may drop its container. The Linux environment carried for the rest is a
  second, temporary path to maintain.
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
- **Everything in containers, the GUI included.** It needs an engine and X or VNC for the
  GUI, which ADR 0013 already rejected for the GUI.
- **Flatpak or Snap.** Their sandboxes block what Puzzle does: start solver processes,
  ssh, container engines, process groups. AppImage may follow for Linux users who want
  one file.
- **A thin installer that downloads solvers on first launch.** Smaller, but not monolithic,
  and it fails offline. It is kept as the optional web installer only.

## Open questions

- Certificates: an Apple Developer ID (yearly fee) and a Windows code-signing option
  (Azure Trusted Signing, an OV certificate). Who holds them?
- Channel host: prefix.dev, anaconda.org or GitHub releases.
- MKL: build PARDISO CalculiX on the user's machine (decision 5), or get a licence
  opinion and ship it prebuilt, or ship SPOOLES only (or a free sparse solver, PaStiX or
  MUMPS through CalculiX's options, if it is fast enough)?
- macOS Intel: build installers for it while Apple supports it, or arm64 only from the
  first release?
- The order of the native ports in decision 4, and whether the carried Linux environment
  is worth building at all, or whether the first releases just list those solvers as
  "not on this system yet".
