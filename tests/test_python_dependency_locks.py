"""Offline dependency-graph checks; actual pip hash enforcement is separate acceptance."""

import zipfile
from pathlib import Path

import pytest
from packaging.version import Version

from scripts import lock_python_dependencies as locks


def wheel(name, requirements=(), version="1.0"):
    return locks.Wheel(name, Version(version), tuple(requirements), "a" * 64)


def test_extras_revisit_dependencies_and_platform_markers():
    wheels = {
        "root": wheel("root", ["child", "child[optional]", 'win-only; sys_platform == "win32"']),
        "child": wheel("child", ['optional-leaf; extra == "optional"', "base-leaf"]),
        "optional-leaf": wheel("optional-leaf"),
        "base-leaf": wheel("base-leaf"),
        "win-only": wheel("win-only"),
    }
    assert locks.closure(["root"], wheels, "windows-py312") == set(wheels)
    assert locks.closure(["root"], wheels, "linux-py312") == set(wheels) - {"win-only"}


@pytest.mark.parametrize(
    "dependency,pattern",
    [
        ("absent>=1", "Missing fixed wheel dependency"),
        ("child>=2", "Fixed version does not satisfy dependency"),
        ("child @ https://example.invalid/private.whl", "Direct URL"),
    ],
)
def test_missing_incompatible_or_unreviewed_dependency_fails_closed(dependency, pattern):
    wheels = {"root": wheel("root", [dependency]), "child": wheel("child")}
    with pytest.raises(ValueError, match=pattern):
        locks.closure(["root"], wheels, "linux-py312")


def test_cycle_and_multiple_roots_preserve_extra_selection():
    wheels = {
        "root": wheel("root", ["child[optional]"]),
        "child": wheel("child", ["root", 'leaf; extra == "optional"']),
        "leaf": wheel("leaf"),
    }
    assert locks.closure(["child", "root"], wheels, "linux-py312") == set(wheels)


def test_render_merges_platform_hashes_and_marks_target_only_packages():
    windows = {"common": wheel("common"), "win-only": wheel("win-only")}
    linux = {"common": locks.Wheel("common", Version("1.0"), (), "b" * 64)}
    rendered = locks.render_lock(
        {"windows-py312": set(windows), "linux-py312": set(linux)},
        {"windows-py312": windows, "linux-py312": linux},
    )
    assert 'win-only==1.0; sys_platform == "win32"' in rendered
    assert rendered.count("--hash=sha256:" + "a" * 64) == 2
    assert rendered.count("--hash=sha256:" + "b" * 64) == 1


@pytest.mark.parametrize("pin", ["a>=1", "a==1\na==1", "a @ https://example.invalid/a.whl"])
def test_constraint_reader_rejects_ranges_duplicate_and_url_pins(tmp_path, pin):
    path = tmp_path / "constraints"
    path.write_text(pin, encoding="utf-8")
    with pytest.raises(ValueError):
        locks.fixed_versions(path)


def write_wheel(directory, filename, metadata):
    path = directory / filename
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("example-1.0.dist-info/METADATA", metadata)
    return path


def test_wheel_metadata_must_match_filename_pin_python_and_target(tmp_path):
    path = write_wheel(
        tmp_path,
        "example-1.0-py3-none-any.whl",
        "Name: different\nVersion: 1.0\n",
    )
    pins = {"example": Version("1.0")}
    with pytest.raises(ValueError, match="filename and metadata disagree"):
        locks.read_wheels(tmp_path, "linux-py312", pins)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "example-1.0.dist-info/METADATA",
            "Name: example\nVersion: 1.0\nRequires-Python: >=3.13\n",
        )
    with pytest.raises(ValueError, match="Python target"):
        locks.read_wheels(tmp_path, "linux-py312", pins)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n")
    with pytest.raises(ValueError, match="fixed version"):
        locks.read_wheels(tmp_path, "linux-py312", {"example": Version("2.0")})
    incompatible = tmp_path / "other-target"
    incompatible.mkdir()
    write_wheel(
        incompatible,
        "example-1.0-cp312-cp312-win_amd64.whl",
        "Name: example\nVersion: 1.0\n",
    )
    with pytest.raises(ValueError, match="Incompatible wheel"):
        locks.read_wheels(incompatible, "linux-py312", pins)


def test_build_wheel_uses_top_level_metadata_and_hashes_actual_bytes(tmp_path):
    path = write_wheel(tmp_path, "example-1.0-py3-none-any.whl", "Name: example\nVersion: 1.0\n")
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("example/vendor/other-9.dist-info/METADATA", "Name: other\nVersion: 9\n")
    first = locks.read_wheels(tmp_path, "linux-py312", {"example": Version("1.0")})
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("example/data.txt", "changed payload")
    second = locks.read_wheels(tmp_path, "linux-py312", {"example": Version("1.0")})
    assert first["example"].sha256 != second["example"].sha256


def test_committed_runtime_does_not_contain_test_or_build_tools():
    runtime = Path("requirements/runtime.lock").read_text(encoding="utf-8")
    for tool in ("pytest", "mypy", "ruff", "setuptools", "librt", "pluggy"):
        assert not any(line.startswith(tool + "==") for line in runtime.splitlines())
    assert "-r runtime.lock" in Path("requirements/dev.lock").read_text(encoding="utf-8")
