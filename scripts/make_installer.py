"""Write Puzzle's installer from the lock file, and build it (ADR 0068, step 2).

    pixi run -e packaging make-installer [--platform P] [--packages DIR | --channel URL]
                                         [--type sh|pkg|exe] [--out DIR] [--build]

An installer is exactly the lock: `construct.yaml` names no specs to solve, only explicit
package lists (`@EXPLICIT`, every package by URL and md5) written from `pixi.lock`.

- **Base environment:** pixi's `app` environment (Puzzle and its GUI, the same versions as
  `dev` by its solve group). The lock has Puzzle, py-slvs and manifold3d from PyPI; here
  they are Puzzle's own conda packages instead (step 1), from a local build
  (`--packages packaging/output`), else the channel on GitHub (`--channel`). Their
  dependencies must already be in the environment, or the installer would be broken;
  that is checked.
- **Extra environments:** each solver's, with pixi's names (`EXTRA`), so the installed
  Puzzle finds them as `<prefix>/envs/<name>` (`jobs.runtime.environment`). Those the
  lock has no packages for on a platform (Code_Aster on macOS) are left out.

Written into `<out>/<platform>/`: construct.yaml, base.txt, envs/<name>.txt and the
licence. `--build` then runs conda constructor there, which fetches every package once
and writes `Puzzle-<version>-<platform>.<sh|pkg|exe>` into `<out>/`.
"""

from __future__ import annotations

import argparse
import json
import os
import platform as host
import re
import subprocess
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "pixi.lock"
CHANNEL = "https://github.com/acmeanvil/Puzzle/releases/download"
BASE = "app"
# Solver and helper environments shipped beside Puzzle, by their pixi names. Not
# calculix-pardiso: CalculiX with PARDISO is built on the user's machine (step 4).
EXTRA = ("calculix", "fenicsx", "code-aster", "netgen", "mmg", "scripts", "runner")
OURS = ("puzzle-fem", "py-slvs", "manifold3d")  # Puzzle's channel, for the lock's PyPI ones
TYPES = {"linux": "sh", "osx": "pkg", "win": "exe"}
# Signing (step 3) is on only when these are set, as CI does from its secrets once the
# certificates exist; until then installers are unsigned test builds.
#   macOS: the "Developer ID Installer: ..." identity signs the .pkg (productsign), the
#     "Developer ID Application: ..." one every Mach-O file constructor puts in it, for
#     notarization (the workflow then submits and staples it).
#   Windows: a .pfx certificate for signtool (its password in
#     CONSTRUCTOR_PFX_CERTIFICATE_PASSWORD), or PUZZLE_WIN_SIGNTOOL=azuresigntool for a
#     certificate in Azure, configured by AZURE_SIGNTOOL_* variables.
SIGNING = {
    "osx": {"signing_identity_name": "PUZZLE_MAC_INSTALLER_IDENTITY",
            "notarization_identity_name": "PUZZLE_MAC_APP_IDENTITY"},
    "win": {"signing_certificate": "PUZZLE_WIN_CERTIFICATE",
            "windows_signing_tool": "PUZZLE_WIN_SIGNTOOL"},
}  # fmt: skip


@dataclass(frozen=True)
class Package:
    url: str
    md5: str
    name: str
    depends: tuple[str, ...] = ()

    def line(self) -> str:
        return f"{self.url}#{self.md5}"


def this_platform() -> str:
    machine = host.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "darwin":
        return "osx-arm64" if arm else "osx-64"
    if sys.platform == "win32":
        return "win-64"
    return "linux-aarch64" if arm else "linux-64"


def _name(url: str) -> str:
    """A conda file name's package name (`name-version-build.conda`)."""
    stem = url.rsplit("/", 1)[1].removesuffix(".conda").removesuffix(".tar.bz2")
    return stem.rsplit("-", 2)[0]


# --- the lock file ------------------------------------------------------------------


@dataclass
class Lock:
    envs: dict[tuple[str, str], list[tuple[str, str]]]  # (env, platform) -> [(kind, url)]
    records: dict[str, dict]  # url -> {md5, sha256, depends}
    virtual: dict[str, list[str]]  # platform -> its virtual packages

    def packages(self, env: str, platform: str) -> tuple[list[Package], list[str]]:
        """An environment's conda packages, and the PyPI ones it also has."""
        conda, pypi = [], []
        for kind, url in self.envs.get((env, platform), []):
            if kind == "pypi":
                pypi.append(url)
                continue
            rec = self.records[url]
            conda.append(Package(url, rec["md5"], _name(url), tuple(rec.get("depends", ()))))
        return conda, pypi


def read_lock(path: Path = LOCK) -> Lock:
    """pixi.lock (format 6+), read line by line: it is 2 MB of YAML that keeps to a few
    fixed shapes, and a YAML parser takes many seconds over it."""
    text = path.read_text(encoding="utf-8")
    head, _, packages = text.partition("\npackages:\n")
    _, _, environments = head.partition("\nenvironments:\n")
    virtual: dict[str, list[str]] = {}
    name = None
    for line in head.partition("\nenvironments:\n")[0].splitlines():
        if m := re.match(r"^- name: (\S+)$", line):
            name = m.group(1)
            virtual[name] = []
        elif (m := re.match(r"^  - (__\S+)$", line)) and name:
            virtual[name].append(m.group(1))
    envs: dict[tuple[str, str], list[tuple[str, str]]] = {}
    env = plat = None
    for line in environments.splitlines():
        if m := re.match(r"^  ([A-Za-z0-9_-]+):$", line):
            env = m.group(1)
        elif m := re.match(r"^      ([a-z0-9-]+):$", line):
            plat = m.group(1)
        elif m := re.match(r"^      - (conda|pypi): (\S+)$", line):
            envs.setdefault((env, plat), []).append((m.group(1), m.group(2)))
    records: dict[str, dict] = {}
    rec: dict | None = None
    key = None
    for line in packages.splitlines():
        if m := re.match(r"^- (conda|pypi): (\S+)$", line):
            rec = records.setdefault(m.group(2), {"depends": []})
            key = None
        elif rec is None:
            continue
        elif m := re.match(r"^  (md5|sha256): (\S+)$", line):
            rec[m.group(1)] = m.group(2)
        elif re.match(r"^  depends:$", line):
            key = "depends"
        elif key == "depends" and (m := re.match(r"^  - (.+)$", line)):
            rec["depends"].append(m.group(1))
        elif re.match(r"^  \S", line):
            key = None
    return Lock(envs, records, virtual)


# --- Puzzle's own packages ----------------------------------------------------------


def _repodata(source: str, subdir: str) -> dict:
    if re.match(r"^https?://", source):
        try:
            with urllib.request.urlopen(f"{source}/{subdir}/repodata.json", timeout=60) as r:
                return json.load(r)
        except OSError:
            return {}
    path = Path(source) / subdir / "repodata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _key(rec: dict) -> tuple:
    parts = [int(p) if p.isdigit() else p for p in re.split(r"[.+-]", rec["version"])]
    return tuple((0, p) if isinstance(p, int) else (-1, p) for p in parts), rec.get(
        "build_number", 0
    )


def ours(sources: list[str], platform: str) -> list[Package]:
    """The newest of each of Puzzle's packages in the first channel (a folder or a URL)
    that has it: a local build first, then the published channel."""
    out = []
    for name in OURS:
        for source in sources:
            best = None
            for subdir in (platform, "noarch"):
                data = _repodata(source, subdir)
                for group in ("packages.conda", "packages"):
                    for fn, rec in data.get(group, {}).items():
                        if rec["name"] == name and (best is None or _key(rec) > _key(best[2])):
                            best = (subdir, fn, rec)
            if best is not None:
                break
        else:
            raise SystemExit(f"{name} is not in {' or '.join(sources)} for {platform}")
        subdir, fn, rec = best
        local = not re.match(r"^https?://", source)
        here = Path(source).resolve() / subdir / fn
        url = here.as_uri() if local else f"{source}/{subdir}/{fn}"
        out.append(Package(url, rec["md5"], name, tuple(rec.get("depends", ()))))
    return out


def unmet(added: list[Package], env: list[Package]) -> list[str]:
    """Dependencies of `added` that nothing in the environment provides (by name;
    virtual packages are the installer's to check)."""
    have = {p.name for p in env} | {p.name for p in added}
    missing = []
    for p in added:
        for dep in p.depends:
            name = dep.split()[0]
            if not name.startswith("__") and name not in have:
                missing.append(f"{p.name} needs {dep}")
    return missing


# --- construct.yaml -----------------------------------------------------------------


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


def _explicit(packages: list[Package]) -> str:
    return "@EXPLICIT\n" + "".join(p.line() + "\n" for p in packages)


def _yaml(value, indent: int = 0) -> str:
    """A small YAML writer for construct.yaml's plain shapes (strings quoted as JSON)."""
    pad = " " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:\n{_yaml(v, indent + 2)}")
            else:
                lines.append(f"{pad}{k}: {_yaml(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        return "\n".join(f"{pad}- {_yaml(v)}" for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value)


def signing(platform: str, environ: dict[str, str] | None = None) -> dict[str, str]:
    """construct.yaml's signing keys for `platform`, from the environment (`SIGNING`)."""
    environ = os.environ if environ is None else environ
    keys = SIGNING.get(platform.split("-")[0], {})
    out = {key: environ[var] for key, var in keys.items() if environ.get(var)}
    if "notarization_identity_name" in out and "signing_identity_name" not in out:
        raise SystemExit("notarizing needs the installer identity too "
                         "(PUZZLE_MAC_INSTALLER_IDENTITY)")  # fmt: skip
    if "signing_certificate" in out:
        out.setdefault("windows_signing_tool", "signtool")
    return out


def write(lock: Lock, platform: str, sources: list[str], out: Path, kind: str,
          environ: dict[str, str] | None = None) -> Path:  # fmt: skip
    """Write the installer's construct.yaml and explicit lists; returns its folder."""
    base, pypi = lock.packages(BASE, platform)
    if not base:
        raise SystemExit(f"pixi.lock has no '{BASE}' environment for {platform}: pixi lock")
    unknown = [u for u in pypi if not any(_pypi_name(u) == n for n in OURS)]
    if unknown:
        raise SystemExit(f"'{BASE}' has PyPI packages with no conda package: {unknown}")
    added = ours(sources, platform)
    if missing := unmet(added, base):
        raise SystemExit("Puzzle's packages need what the lock lacks:\n  " + "\n  ".join(missing))
    folder = out / platform
    (folder / "envs").mkdir(parents=True, exist_ok=True)
    (folder / "base.txt").write_text(_explicit(base + added), encoding="utf-8")
    extra = {}
    for name in EXTRA:
        packages, pypi = lock.packages(name, platform)
        if not packages:
            continue
        if pypi:
            raise SystemExit(f"'{name}' has PyPI packages, which an installer can't carry")
        (folder / "envs" / f"{name}.txt").write_text(_explicit(packages), encoding="utf-8")
        extra[name] = {"environment_file": f"envs/{name}.txt", "menu_packages": []}
    (folder / "LICENSE.txt").write_text(
        (ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8"
    )
    info = {
        "name": "Puzzle",
        "version": version(),
        "company": "Puzzle",
        "installer_type": kind,
        "installer_filename": f"Puzzle-{version()}-{platform}.{kind}",
        "environment_file": "base.txt",
        "extra_envs": extra,
        "menu_packages": ["puzzle-fem"],
        "license_file": "LICENSE.txt",
        "initialize_conda": False,  # Puzzle is an app, not a conda for the shell
        "register_python": False,
        "keep_pkgs": False,
        # The systems the lock was solved for are the oldest its packages accept.
        "virtual_specs": [_at_least(v) for v in lock.virtual.get(platform, []) if _bounded(v)],
    }
    if platform.startswith("win"):
        info["default_prefix"] = "%LOCALAPPDATA%\\Puzzle"  # per user, no administrator
    info.update(signing(platform, environ))
    (folder / "construct.yaml").write_text(
        f"# Written by scripts/make_installer.py from pixi.lock ({platform}); do not edit.\n"
        + _yaml(info)
        + "\n",
        encoding="utf-8",
    )
    return folder


def _pypi_name(url: str) -> str:
    if url in ("./", "."):
        return "puzzle-fem"
    return url.rsplit("/", 1)[-1].split("-", 1)[0].replace("_", "-").lower()


def _bounded(virtual: str) -> bool:
    return virtual.split("=")[0] in ("__glibc", "__osx")


def _at_least(virtual: str) -> str:
    name, ver = virtual.split("=")[:2]
    return f"{name}>={ver}"


def build(folder: Path, out: Path) -> Path:
    conda = Path(sys.prefix) / "standalone_conda" / "conda.exe"
    argv = ["constructor", str(folder), "--output-dir", str(out)]
    if conda.exists():
        argv += ["--conda-exe", str(conda)]
    print("+", " ".join(argv), flush=True)
    subprocess.run(argv, check=True, cwd=folder)
    info = (folder / "construct.yaml").read_text(encoding="utf-8")
    filename = json.loads(re.search(r"^installer_filename: (.+)$", info, re.M).group(1))
    return out / filename


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--platform", default=this_platform())
    p.add_argument("--packages", type=Path, help="a local channel (build-packages' output), "
                   "taken before the published one")  # fmt: skip
    p.add_argument("--channel", default=CHANNEL, help="Puzzle's channel (default: GitHub's)")
    p.add_argument("--type", choices=("sh", "pkg", "exe"), help="default: pkg, exe or sh")
    p.add_argument("--out", type=Path, default=ROOT / "packaging" / "installer")
    p.add_argument("--build", action="store_true", help="run constructor too")
    args = p.parse_args(argv)
    kind = args.type or TYPES[args.platform.split("-")[0]]
    sources = [str(args.packages)] if args.packages else []
    sources.append(args.channel.rstrip("/"))
    steps = 2 if args.build else 1
    print(f"[progress] 1/{steps} writing construct.yaml for {args.platform}", flush=True)
    folder = write(read_lock(), args.platform, sources, args.out, kind)
    print(f"wrote {folder / 'construct.yaml'}")
    if args.build:
        print(f"[progress] 2/{steps} building the {kind} installer", flush=True)
        built = build(folder, args.out)
        print(f"built {built} ({built.stat().st_size / 1e9:.2f} GB)")
    print(f"[progress] {steps}/{steps} done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
