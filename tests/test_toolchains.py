"""app_release.builders.toolchains' choice of the Python that runs a build
script; no interpreter is started."""

import os

import pytest

from app_release.builders import toolchains
from app_release.model import BuildRecord


@pytest.fixture
def probes(monkeypatch):
    """Stubs probe() and _find_version(); returns the version lookups made.
    probe() answers for any path that exists, and for "/given"."""
    looked_up = []

    def fake_probe(command):
        path = command[0]
        if path == "/given" or os.path.isfile(path):
            return path, "3.11.9"
        return None

    def fake_find_version(version):
        looked_up.append(version)
        return f"/found/python{version}", f"{version}.1"

    monkeypatch.setattr(toolchains, "probe", fake_probe)
    monkeypatch.setattr(toolchains, "_find_version", fake_find_version)
    return looked_up


def _record(python):
    return BuildRecord(path="build_info.json", written_at=0, toolchain={"Python": python})


def _make_venv(repo):
    path = toolchains._venv_python(os.path.join(str(repo), toolchains.VENV_DIR))
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8"):
        pass
    return path


def _pin(repo, text):
    (repo / toolchains.PIN_FILE).write_text(text, encoding="utf-8")


def test_explicit_python_wins(probes, tmp_path):
    _make_venv(tmp_path)
    _pin(tmp_path, "3.10\n")
    path, reason = toolchains.build_python(str(tmp_path), "/given", _record("3.12.0"))
    assert path == "/given" and "--toolchain" in reason
    assert probes == []


def test_explicit_python_that_does_not_run_is_refused(probes, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        toolchains.build_python(str(tmp_path), "/missing")
    assert "/missing" in str(excinfo.value)


def test_existing_build_venv_comes_before_a_pin(probes, tmp_path):
    venv_python = _make_venv(tmp_path)
    _pin(tmp_path, "3.11\n")
    path, reason = toolchains.build_python(str(tmp_path), None, _record("3.10.4"))
    assert path == venv_python and toolchains.VENV_DIR in reason
    assert probes == []


def test_a_venv_on_another_version_than_the_pin_is_warned_about(probes, tmp_path, caplog):
    _make_venv(tmp_path)
    _pin(tmp_path, "3.10\n")
    with caplog.at_level("WARNING", logger="app_release"):
        toolchains.build_python(str(tmp_path))
    assert any("3.10" in message and "3.11.9" in message for message in caplog.messages)


def test_pin_comes_before_the_last_build(probes, tmp_path):
    _pin(tmp_path, "# comment\n3.11.9\n")
    path, reason = toolchains.build_python(str(tmp_path), None, _record("3.10.4"))
    assert path == "/found/python3.11" and toolchains.PIN_FILE in reason
    assert probes == ["3.11"]


def test_last_build_version_is_the_fallback(probes, tmp_path):
    path, reason = toolchains.build_python(str(tmp_path), None, _record("3.10.4"))
    assert path == "/found/python3.10" and "last build" in reason


def test_nothing_to_go_by_is_refused(probes, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        toolchains.build_python(str(tmp_path), None, None)
    assert "--toolchain" in str(excinfo.value)


def test_a_version_that_is_not_installed_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(toolchains, "_find_version", lambda version: None)
    _pin(tmp_path, "3.13\n")
    with pytest.raises(SystemExit) as excinfo:
        toolchains.build_python(str(tmp_path))
    assert "3.13" in str(excinfo.value)


@pytest.mark.parametrize("text, version", [
    ("3.11\n", "3.11"), ("3.11.9", "3.11"), ("cpython-3.12.1\n", "3.12"), ("\n# only a comment\n", ""),
])
def test_pinned_version(tmp_path, text, version):
    _pin(tmp_path, text)
    assert toolchains.pinned_version(str(tmp_path)) == version


def test_unreadable_pin_is_refused(tmp_path):
    _pin(tmp_path, "system\n")
    with pytest.raises(SystemExit):
        toolchains.pinned_version(str(tmp_path))
