"""The command builder, end to end with a real build command: a Python
one-liner that writes a zip. git is stubbed for the build record."""

import os
import sys

import pytest

from app_release import pipeline, platforms, vcs
from app_release.builders.command import CommandBuilder
from app_release.model import ARCHIVE, PACKAGE
from helpers import HEAD, TREE, Options, checkout_git

WRITE_ZIP = ("import os, zipfile; os.makedirs('dist', exist_ok=True); "
             "zipfile.ZipFile('dist/tool.zip', 'w').writestr('tool.txt', 'built')")


def _builder(tmp_path, **table):
    table = {"build": [[sys.executable, "-c", WRITE_ZIP]],
             "artifacts": [{"glob": "dist/*.zip", "kind": "archive"}], **table}
    return CommandBuilder(str(tmp_path), {"builder": "command", "command": table}, Options())


def test_detect_needs_build_commands(tmp_path):
    assert _builder(tmp_path).detect()
    assert not CommandBuilder(str(tmp_path), {}, Options()).detect()


def test_a_single_command_needs_no_outer_list(tmp_path):
    builder = _builder(tmp_path, build=[sys.executable, "-c", "pass"])
    assert [r.command for r in builder.plan([], None)] == [[sys.executable, "-c", "pass"]]


def test_a_malformed_build_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        _builder(tmp_path, build="make dist").plan([], None)


def test_toolchain_is_refused(tmp_path):
    builder = CommandBuilder(str(tmp_path), {"command": {"build": ["make"]}}, Options(toolchain="x"))
    with pytest.raises(SystemExit):
        builder.plan([], None)


def test_artifacts_from_globs(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "tool.zip").write_bytes(b"x")
    (tmp_path / "dist" / "tool-1.0.0.gem").write_bytes(b"x")
    builder = _builder(tmp_path, artifacts=["dist/*.zip", {"glob": "dist/*.gem", "kind": "package",
                                                           "product": "toolgem"}])
    found = builder.artifacts()
    assert [(a.product, a.kind, a.platform) for a in found] == [
        ("tool", ARCHIVE, platforms.host()), ("toolgem", PACKAGE, None)]


def test_no_artifacts_configured_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        _builder(tmp_path, artifacts=[]).artifacts()


def test_build_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git())
    builder = _builder(tmp_path, toolchain_versions=[[sys.executable, "--version"]])
    assert builder.artifacts() == []
    # No artifacts yet: nothing is behind, so plan and build directly, as a first build.
    builder.build(builder.plan([], None), [], "1.0.0", {})
    artifacts = builder.artifacts()
    assert [a.product for a in artifacts] == ["tool"]
    record = builder.read_record()
    assert record.version == "1.0.0" and record.commit == HEAD and record.tree == TREE
    assert os.path.basename(sys.executable) in record.toolchain
    # The fresh build is current, and passes the checks.
    assert pipeline.behind(str(tmp_path), artifacts[0], "1.0.0", record, HEAD, TREE, 0) is None
    errors, _ = pipeline.check(str(tmp_path), "1.0.0", "v1.0.0", record, artifacts, None, {}, None)
    assert errors == []
