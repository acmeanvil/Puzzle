# Packaging

How Puzzle gets from this repository to an installed, working system (ADR 0068).

| Folder | What |
| --- | --- |
| [recipes/](recipes/) | Puzzle's own conda packages, built with rattler-build and published to Puzzle's channel on GitHub |
| [conda-forge/](conda-forge/README.md) | Recipes meant for conda-forge itself (elmerfem), developed here and submitted there |

## Puzzle's conda packages

| Recipe | What | Built from |
| --- | --- | --- |
| [puzzle-fem](recipes/puzzle-fem/recipe.yaml) | Puzzle itself, noarch, with every dependency the `dev` environment runs on (pixi.toml's default and gui features; `tests/test_packaging.py` keeps them in step) | this repository |
| [py-slvs](recipes/py-slvs/recipe.yaml) | SolveSpace's constraint solver (sketches) | PyPI sdist, SWIG |
| [manifold3d](recipes/manifold3d/recipe.yaml) | Booleans on faceted meshes | PyPI sdist, nanobind, TBB; Clipper2 fetched at manifold's pinned commit |

Build them into a local channel (`packaging/output/`, ignored by git):

```sh
pixi run -e packaging build-packages                 # all three, in order
pixi run -e packaging build-packages --only py-slvs  # one
```

Each package's tests run as part of its build (imports, a solved sketch system, a cube's
volume, `puzzle --help`). [variants.yaml](recipes/variants.yaml) holds what conda-forge's
global pinning would give: Python 3.12, the compilers, and the C library and macOS targets
(macOS 11 on Intel: current libc++ refuses older).

## The channel

`https://github.com/acmeanvil/Puzzle/releases/download`. A conda channel is read as
`<channel>/<platform>/repodata.json`, so each platform (`linux-64`, `linux-aarch64`,
`osx-64`, `osx-arm64`, `win-64`, `noarch`) is a GitHub release tagged with its name,
holding its packages and its index. The [Packages workflow](../.github/workflows/packages.yml)
builds on all five platforms when the recipes change, and publishes on a published release
or when run by hand with "publish" ticked (`scripts/publish_channel.py`). Publishing adds
packages and re-indexes; it never replaces a published package, since lock files pin its
hash. Puzzle's build number is the workflow's run number.

To use it:

```sh
pixi add --channel https://github.com/acmeanvil/Puzzle/releases/download puzzle-fem
```

Found while building (macOS Intel):

- SolveSpace's CMakeLists asks for CMake 2.8: `CMAKE_POLICY_VERSION_MINIMUM=3.5`.
- scikit-build-core gives `cmake -E capabilities` 5 s (20 s with `CI` set). macOS checks a
  freshly installed binary on its first runs, which took longer, so the CMake search failed
  as "not found". The recipe sets `CI` and runs CMake once first.
- Clipper2 at manifold's pinned commit uses `std::back_inserter` without `<iterator>`, which
  newer libc++ no longer includes by itself.

## The installers

`scripts/make_installer.py` writes conda constructor's `construct.yaml` from `pixi.lock`
(`packaging/installer/<platform>/`, ignored by git), and `--build` builds it:

```sh
pixi run -e packaging make-installer --build                       # this platform, Puzzle's channel
pixi run -e packaging make-installer --packages packaging/output --type sh --build  # a local puzzle-fem first
pixi run -e packaging python scripts/smoke_installer.py packaging/installer/Puzzle-0.0.1-osx-64.sh
```

- An installer is exactly the lock: explicit package lists (`@EXPLICIT`, URL and md5), no
  solving. The base environment is pixi's `app` environment (Puzzle and its GUI, in the
  `main` solve group, so the same versions as `dev`, plus conda, which constructor needs
  for extra environments and the Solvers page's Repair will use); the lock's PyPI entries (Puzzle,
  py-slvs, manifold3d) become Puzzle's conda packages, whose dependencies must already be
  in the lock.
- Each solver environment (`calculix`, `fenicsx`, `code-aster`, `netgen`, `mmg`,
  `scripts`, `runner`) is an extra environment with pixi's name, installed as
  `<prefix>/envs/<name>`, where `jobs.runtime.environment` finds it. Those the lock lacks
  on a platform are left out (Code_Aster on macOS, until decision 4's Linux machine).
- puzzle-fem carries a menuinst entry (`recipes/puzzle-fem/menu.json`): the Start menu
  and desktop on Windows (with `.puzzle` archives opening in it), an app in
  `~/Applications` on macOS, a desktop entry on Linux.
- Per user, no administrator; the installer asks nothing but where to install.
- The smoke test installs in batch mode into a temporary folder (no shortcuts unless
  `--shortcuts`), runs `puzzle --help`, solves the cantilever's Tip load with the
  installed CalculiX with nothing on PATH, and checks each solver environment answers.
