"""app_release.record: reading both layouts, and the tool-written record."""

import json
import os
import sys

import pytest

from app_release import record as records, vcs
from app_release.builders.base import Builder
from app_release.model import BuildRun
from helpers import HEAD, TREE, fake_git, write_record


def test_the_nuitka_layout_is_normalized(tmp_path):
    path = write_record(tmp_path, commit=HEAD, python="3.11.9", packages={"torch": "2.4.0"},
                        with_oqs=True, build_id="20261017-120000")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["source"]["repo"] = "C:\\repos\\App"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    record = records.load(path)
    assert record.commit == HEAD and record.tree == TREE and record.changes == ""
    assert record.toolchain == {"Python": "3.11.9"}
    assert record.dependencies == {"torch": "2.4.0"}
    assert record.features == {"oqs": True}
    assert record.embedded_paths == ["C:\\repos\\App"]
    assert record.written_at == os.path.getmtime(path)


def test_a_missing_record_is_none(tmp_path):
    assert records.load(str(tmp_path / "none.json")) is None


def test_a_record_without_a_schema_is_read(tmp_path):
    path = tmp_path / "build_info.json"
    path.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    assert records.load(str(path)).version == "1.0.0"


def test_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "build_info.json"
    path.write_text(json.dumps({"schema": records.SCHEMA + 1}), encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        records.load(str(path))
    assert "Update app-release" in str(excinfo.value)


def test_restore_dates_the_record_before_the_artifacts(tmp_path):
    from helpers import built
    _, artifact = built(tmp_path)
    (tmp_path / "unpacked").mkdir()
    source = write_record(tmp_path / "unpacked", commit=HEAD)
    target = tmp_path / "restored" / "build_info.json"
    records.restore(source, str(target), [artifact])
    assert os.path.getmtime(target) < os.path.getmtime(artifact.path)
    assert records.load(str(target)).commit == HEAD


class _ToolBuilder(Builder):
    """A builder whose build is a Python one-liner, with a tool-written record."""
    name = "fake"

    def detect(self):
        return True

    def artifacts(self):
        return []

    def plan(self, stale, record):
        return []

    def toolchain_versions(self):
        return {"fake": "1.0"}


def _run(tmp_path, code):
    return BuildRun(command=[sys.executable, "-c", code], cwd=str(tmp_path))


def test_the_tool_writes_the_record_after_a_successful_build(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(answers={("status", "--porcelain", "--untracked-files=no"): ""}))
    builder = _ToolBuilder(str(tmp_path), {}, None)
    out = tmp_path / "out.bin"
    builder.build([_run(tmp_path, f"open({str(out)!r}, 'wb').write(b'x')")], ["--flag"], "1.2.0", {"oqs": False})
    record = builder.read_record()
    assert record.version == "1.2.0" and record.commit == HEAD and record.tree == TREE and record.changes == ""
    assert record.toolchain == {"fake": "1.0"} and record.features == {"oqs": False}
    assert record.options == ["--flag"]
    # Dated at the build's start, so what the build wrote is not older than it.
    assert record.written_at <= os.path.getmtime(out)


def test_a_failed_build_leaves_the_previous_record(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(answers={("status", "--porcelain", "--untracked-files=no"): ""}))
    builder = _ToolBuilder(str(tmp_path), {}, None)
    builder.build([_run(tmp_path, "pass")], [], "1.0.0", {})
    with pytest.raises(SystemExit):
        builder.build([_run(tmp_path, "raise SystemExit(3)")], [], "2.0.0", {})
    assert builder.read_record().version == "1.0.0"
