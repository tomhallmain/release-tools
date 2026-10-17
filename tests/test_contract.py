"""What every builder and destination must do, checked against a fixture repo
for each. A new backend gets a fixture here."""

import os
import sys

import pytest

from app_release.builders import BUILDERS
from app_release.destinations import DESTINATIONS
from app_release.model import KINDS, ReleasePlan
from app_release.versions import VersionSource
from helpers import HEAD, Options


def _nuitka(repo):
    (repo / "build_exe.py").write_text("", encoding="utf-8")
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (repo / "dist").mkdir()
    exe = repo / "dist" / ("App.exe" if sys.platform == "win32" else "App")
    exe.write_bytes(b"x")
    os.chmod(exe, 0o755)
    return {}


def _command(repo):
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (repo / "out").mkdir()
    (repo / "out" / "app.tar.gz").write_bytes(b"x")
    return {"command": {"build": [["true"]], "artifacts": [{"glob": "out/*.tar.gz"}]}}


def _cargo(repo):
    (repo / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "1.0.0"\n', encoding="utf-8")
    folder = repo / "target" / "release"
    folder.mkdir(parents=True)
    (folder / ("app.exe" if sys.platform == "win32" else "app")).write_bytes(b"x")
    return {}


FIXTURES = {"nuitka": _nuitka, "command": _command, "cargo": _cargo}


def test_every_builder_has_a_fixture():
    assert set(FIXTURES) == set(BUILDERS)


@pytest.fixture(params=sorted(BUILDERS))
def builder(request, tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    config = FIXTURES[request.param](tmp_path)
    return BUILDERS[request.param](str(tmp_path), config, Options())


def test_detects_its_fixture(builder):
    assert builder.detect()
    assert builder.describe()


def test_finds_the_fixture_artifacts(builder):
    artifacts = builder.artifacts()
    assert len(artifacts) == 1
    assert artifacts[0].kind in KINDS and os.path.exists(artifacts[0].path)


def test_reads_no_record_before_a_build(builder):
    assert builder.read_record() is None


def test_paths_are_inside_the_repo(builder):
    for path in (builder.record_path(), builder.log_dir(), builder.stage_dir("v1.0.0")):
        assert os.path.commonpath([builder.repo, path]) == builder.repo


def test_version_source_round_trip(builder):
    source = builder.default_version_source()
    assert isinstance(source, VersionSource)
    with open(source.path(), encoding="utf-8", newline="") as f:
        before = f.read()
    assert source.read() == "1.0.0"
    source.write("1.0.1")
    assert source.read() == "1.0.1"
    with open(source.path(), encoding="utf-8", newline="") as f:
        assert f.read() == before.replace("1.0.0", "1.0.1", 1)


def test_no_detection_in_an_empty_repo(tmp_path):
    for cls in BUILDERS.values():
        assert not cls(str(tmp_path), {}, Options()).detect()


@pytest.mark.parametrize("name", sorted(DESTINATIONS))
def test_destinations_print_a_dry_run(name, tmp_path):
    destination = DESTINATIONS[name](str(tmp_path), {})
    release = ReleasePlan(repo=str(tmp_path), tag="v1.0.0", commit=HEAD, remote="origin",
                          title="App 1.0.0", assets=[], notes="RELEASE_NOTES.md")
    assert destination.dry_run_lines(release)
    assert destination.reads_notes([]) is False
