"""Derive wheel-only hash locks offline, without changing selected versions.

Supported targets are CPython 3.12, Windows amd64 and Linux glibc >= 2.28
amd64. Wheel authenticity is a separate review step; this command hashes bytes,
checks their metadata, and never accesses a package index or executes a wheel.
Use --emit-patch and review/apply its output to update the committed locks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("windows-py312", "linux-py312")


@dataclass(frozen=True)
class Wheel:
    name: str
    version: Version
    requirements: tuple[str, ...]
    sha256: str


def environment(target: str) -> dict[str, str]:
    if target not in TARGETS:
        raise ValueError("Unsupported wheel target")
    windows = target == "windows-py312"
    return {
        **default_environment(),
        "implementation_name": "cpython",
        "implementation_version": "3.12.0",
        "os_name": "nt" if windows else "posix",
        "platform_machine": "AMD64" if windows else "x86_64",
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_system": "Windows" if windows else "Linux",
        "platform_version": "",
        "python_full_version": "3.12.0",
        "python_version": "3.12",
        "sys_platform": "win32" if windows else "linux",
    }


def allowed_tags(target: str) -> set:
    environment(target)
    platforms = (
        ["win_amd64"]
        if target == "windows-py312"
        else [f"manylinux_2_{minor}_x86_64" for minor in range(28, 4, -1)]
        + ["manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64"]
    )
    return set(cpython_tags((3, 12), abis=["cp312"], platforms=platforms)) | set(
        compatible_tags((3, 12), interpreter="cp312", platforms=platforms)
    )


def fixed_versions(path: Path) -> dict[str, Version]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        pins = list(requirement.specifier)
        if requirement.url or requirement.extras or len(pins) != 1 or pins[0].operator != "==":
            raise ValueError("Version constraints must contain exact package pins")
        name = canonicalize_name(requirement.name)
        if name in result:
            raise ValueError("Duplicate version constraint")
        result[name] = Version(pins[0].version)
    return result


def read_wheels(directory: Path, target: str, pins: dict[str, Version]) -> dict[str, Wheel]:
    result = {}
    compatible = allowed_tags(target)
    for path in sorted(directory.glob("*.whl")):
        name, version, _, tags = parse_wheel_filename(path.name)
        if not tags & compatible:
            raise ValueError(f"Incompatible wheel for {target}: {path.name}")
        if name not in pins or version != pins[name]:
            raise ValueError(f"Wheel differs from fixed version: {name}")
        with zipfile.ZipFile(path) as archive:
            files = [
                entry
                for entry in archive.infolist()
                if entry.filename.endswith(".dist-info/METADATA")
                and len(entry.filename.split("/")) == 2
            ]
            if len(files) != 1 or files[0].file_size > 1024 * 1024:
                raise ValueError("Expected one bounded wheel metadata record")
            metadata = BytesParser().parsebytes(archive.read(files[0]))
        if canonicalize_name(metadata["Name"]) != name or Version(metadata["Version"]) != version:
            raise ValueError("Wheel filename and metadata disagree")
        requires_python = metadata.get("Requires-Python")
        if requires_python:
            from packaging.specifiers import SpecifierSet

            if Version("3.12.0") not in SpecifierSet(requires_python):
                raise ValueError("Wheel does not support the locked Python target")
        if name in result:
            raise ValueError(f"Multiple wheels for package and target: {name}")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        result[name] = Wheel(
            name,
            version,
            tuple(metadata.get_all("Requires-Dist", [])),
            digest,
        )
    return result


def closure(roots: list[str], wheels: dict[str, Wheel], target: str) -> set[str]:
    env = environment(target)
    selected: dict[str, set[str]] = {}
    pending = [(Requirement(root), "") for root in roots]
    while pending:
        requirement, parent_extra = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({**env, "extra": parent_extra}):
            continue
        if requirement.url:
            raise ValueError("Direct URL dependencies are not supported")
        name = canonicalize_name(requirement.name)
        wheel = wheels.get(name)
        if wheel is None:
            raise ValueError(f"Missing fixed wheel dependency: {name} ({target})")
        if wheel.version not in requirement.specifier:
            raise ValueError(f"Fixed version does not satisfy dependency: {name}")
        extras = {"", *requirement.extras}
        newly_selected = extras - selected.get(name, set())
        if not newly_selected:
            continue
        selected.setdefault(name, set()).update(newly_selected)
        for extra in newly_selected:
            pending.extend((Requirement(child), extra) for child in wheel.requirements)
    return set(selected)


def render_lock(
    selected: dict[str, set[str]],
    wheels: dict[str, dict[str, Wheel]],
    *,
    include_runtime: bool = False,
) -> str:
    lines = [
        "# Generated offline by scripts/lock_python_dependencies.py; selected versions are frozen.",
        "# CPython 3.12: Windows amd64 / Linux glibc >= 2.28 amd64; wheels only.",
        "# Install with pip --require-hashes --only-binary=:all: -r this-file.",
    ]
    if include_runtime:
        lines.extend(["-r runtime.lock", ""])
    for name in sorted(set().union(*selected.values())):
        targets = [target for target in TARGETS if name in selected[target]]
        versions = {wheels[target][name].version for target in targets}
        if len(versions) != 1:
            raise ValueError("Target package versions differ")
        marker = ""
        if len(targets) == 1:
            marker = (
                '; sys_platform == "win32"'
                if targets[0] == TARGETS[0]
                else '; sys_platform == "linux"'
            )
        hashes = sorted({wheels[target][name].sha256 for target in targets})
        lines.append(f"{name}=={versions.pop()}{marker} \\")
        lines.extend(
            f"    --hash=sha256:{digest}" + (" \\" if index < len(hashes) - 1 else "")
            for index, digest in enumerate(hashes)
        )
    return "\n".join(lines) + "\n"


def generate(root: Path = ROOT) -> dict[str, str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pins = fixed_versions(root / "requirements.lock")
    wheel_root = root / ".cache/python-wheels"
    wheels = {target: read_wheels(wheel_root / target, target, pins) for target in TARGETS}
    runtime = {
        target: closure(project["project"]["dependencies"], wheels[target], target)
        for target in TARGETS
    }
    dev = {
        target: closure(project["project"]["optional-dependencies"]["dev"], wheels[target], target)
        - runtime[target]
        for target in TARGETS
    }
    build_roots = project["build-system"]["requires"]
    build_pins = {}
    for root_requirement in build_roots:
        requirement = Requirement(root_requirement)
        specs = list(requirement.specifier)
        if requirement.url or len(specs) != 1 or specs[0].operator != "==":
            raise ValueError("Build backend must be exactly pinned")
        build_pins[canonicalize_name(requirement.name)] = Version(specs[0].version)
    build_wheels = {
        target: read_wheels(wheel_root / "build", target, build_pins) for target in TARGETS
    }
    build = {target: closure(build_roots, build_wheels[target], target) for target in TARGETS}
    return {
        "requirements/runtime.lock": render_lock(runtime, wheels),
        "requirements/dev.lock": render_lock(dev, wheels, include_runtime=True),
        "requirements/build.lock": render_lock(build, build_wheels),
    }


def patch_for(contents: dict[str, str], root: Path = ROOT) -> str:
    patch = ["*** Begin Patch"]
    for relative, content in contents.items():
        path = root / relative
        if path.exists():
            if path.read_text(encoding="utf-8") == content:
                continue
            patch.extend(
                [f"*** Delete File: {path.as_posix()}", f"*** Add File: {path.as_posix()}"]
            )
        else:
            patch.append(f"*** Add File: {path.as_posix()}")
        patch.extend("+" + line for line in content.splitlines())
    patch.append("*** End Patch")
    return "\n".join(patch)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--emit-patch", action="store_true")
    modes.add_argument("--json", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contents = generate()
    if args.emit_patch:
        print(patch_for(contents))
    elif args.json:
        print(json.dumps(contents))
    else:
        if any(
            not (ROOT / path).exists() or (ROOT / path).read_text(encoding="utf-8") != content
            for path, content in contents.items()
        ):
            raise SystemExit("Python hash locks differ; review --emit-patch output")
        print("PASS: fixed wheel metadata and all three hash locks agree")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"Hash lock generation failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
