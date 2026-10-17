"""app_release.versions: each source reads its value and writes only that value."""

import pytest

from app_release import vcs, versions
from helpers import HEAD


def _round_trip(source, path, expected, text):
    assert source.read() == expected
    source.write("2.0.0")
    assert source.read() == "2.0.0"
    assert path.read_text(encoding="utf-8") == text.replace(expected, "2.0.0", 1)


def test_file(tmp_path):
    path = tmp_path / "VERSION"
    path.write_text("1.4.2\n", encoding="utf-8")
    _round_trip(versions.FileVersion(str(tmp_path), "VERSION"), path, "1.4.2", "1.4.2\n")


def test_pattern(tmp_path):
    text = '"""Version."""\nAPP_VERSION = "1.4.2"\nOTHER = "1.4.2"\n'
    path = tmp_path / "version.py"
    path.write_text(text, encoding="utf-8")
    source = versions.PatternVersion(str(tmp_path), "version.py", r'^APP_VERSION = "([^"]+)"')
    _round_trip(source, path, "1.4.2", text)
    assert 'OTHER = "1.4.2"' in path.read_text(encoding="utf-8")


def test_a_pattern_needs_one_group(tmp_path):
    with pytest.raises(SystemExit):
        versions.PatternVersion(str(tmp_path), "x", r"(a)(b)")


CARGO = '''[package]
name = "tool"
version = "1.4.2"   # the release version

[dependencies]
serde = { version = "1.0.0" }

[[bin]]
name = "tool"
'''


def test_toml(tmp_path):
    path = tmp_path / "Cargo.toml"
    path.write_text(CARGO, encoding="utf-8")
    _round_trip(versions.TomlVersion(str(tmp_path), "Cargo.toml", "package.version"), path, "1.4.2", CARGO)
    assert 'serde = { version = "1.0.0" }' in path.read_text(encoding="utf-8")


def test_toml_workspace_version(tmp_path):
    text = '[workspace.package]\nversion = "0.3.0"\n\n[package]\nname = "tool"\nversion.workspace = true\n'
    path = tmp_path / "Cargo.toml"
    path.write_text(text, encoding="utf-8")
    _round_trip(versions.TomlVersion(str(tmp_path), "Cargo.toml", "package.version"), path, "0.3.0", text)


def test_pyproject(tmp_path):
    text = '[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "app"\nversion = "1.0.0"\n'
    path = tmp_path / "pyproject.toml"
    path.write_text(text, encoding="utf-8")
    _round_trip(versions.TomlVersion(str(tmp_path), "pyproject.toml", "project.version"), path, "1.0.0", text)


def test_json(tmp_path):
    text = '{\n  "name": "app",\n  "version": "1.0.0",\n  "deps": {"x": "1.0.0"}\n}\n'
    path = tmp_path / "package.json"
    path.write_text(text, encoding="utf-8")
    _round_trip(versions.JsonVersion(str(tmp_path), "package.json"), path, "1.0.0", text)


@pytest.mark.parametrize("spec, cls", [
    ("VERSION", versions.FileVersion),
    ({"file": "VERSION"}, versions.FileVersion),
    ({"pattern": {"file": "v.py", "regex": "V = '(.+)'"}}, versions.PatternVersion),
    ({"toml": {"file": "Cargo.toml", "key": "package.version"}}, versions.TomlVersion),
    ({"json": {"file": "package.json"}}, versions.JsonVersion),
])
def test_from_config(tmp_path, spec, cls):
    assert isinstance(versions.from_config(str(tmp_path), spec), cls)


def test_from_config_refuses_other_shapes(tmp_path):
    assert versions.from_config(str(tmp_path), None) is None
    with pytest.raises(SystemExit):
        versions.from_config(str(tmp_path), {"yaml": "x"})


def test_bumped():
    assert versions.bumped("1.4.2", "major") == "2.0.0"
    assert versions.bumped("1.4.2", "minor") == "1.5.0"
    assert versions.bumped("1.4", "patch") == "1.4.1"


def test_bump_keeps_an_untagged_version(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", lambda repo, *args: HEAD if args == ("rev-parse", "HEAD") else None)
    source = versions.FileVersion(str(tmp_path), "VERSION")
    assert versions.bump(str(tmp_path), source, "1.0.0", "minor", no_build=False) == "1.0.0"


def test_bump_commits_the_new_version(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    commits = []

    def git(repo, *args):
        if args == ("rev-parse", "HEAD") or args[:2] == ("rev-parse", "--short=10"):
            return HEAD
        if args == ("rev-parse", "-q", "--verify", "refs/tags/v1.0.0"):
            return HEAD
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return ""
        if args[0] == "commit":
            commits.append(args)
            return ""
        return None

    monkeypatch.setattr(vcs, "git", git)
    source = versions.FileVersion(str(tmp_path), "VERSION")
    assert versions.bump(str(tmp_path), source, "1.0.0", "minor", no_build=False) == "1.1.0"
    assert (tmp_path / "VERSION").read_text(encoding="utf-8") == "1.1.0\n"
    assert commits and commits[0][:3] == ("commit", "-m", "Version 1.1.0")
