"""The nuitka builder: its paths, version lookup, build runs and the
memory-wait plugin contract with the apps' build_exe.py."""

import os
import sys

import pytest

from app_release import cli
from app_release.builders import base, nuitka
from app_release.model import EXECUTABLE, Artifact
from helpers import Options, recording_run


def _builder(tmp_path, script='"--with-headless"', app_name="App", **options):
    (tmp_path / "build_exe.py").write_text(script, encoding="utf-8")
    return nuitka.NuitkaBuilder(str(tmp_path), {"app_name": app_name}, Options(**options))


def _stale(*products):
    return [Artifact(p, f"{p}.exe", EXECUTABLE, "windows-x64") for p in products]


@pytest.fixture
def chosen(monkeypatch):
    """Stubs the interpreter choice; returns the --toolchain values it was asked with."""
    asked = []

    def fake_build_python(repo, explicit, record):
        asked.append(explicit)
        return "/usr/bin/python3.11", "stubbed"

    monkeypatch.setattr(nuitka, "build_python", fake_build_python)
    return asked


def test_paths_are_those_build_exe_uses(tmp_path):
    builder = _builder(tmp_path)
    assert builder.record_path() == os.path.join(str(tmp_path), "build", "nuitka", "build_info.json")
    assert builder.log_dir() == os.path.join(str(tmp_path), "build", "logs")
    assert builder.stage_dir("v1.0.0") == os.path.join(str(tmp_path), "dist", "release", "v1.0.0")
    assert builder.record_owner == "app"


def test_a_rebuild_runs_build_exe_with_the_chosen_python_and_the_plugin(tmp_path, chosen):
    runs = _builder(tmp_path, script="", toolchain="/opt/py311/python").plan(_stale("App"), None)
    assert chosen == ["/opt/py311/python"]
    assert [r.command for r in runs] == [["/usr/bin/python3.11", os.path.join(str(tmp_path), "build_exe.py")]]
    assert runs[0].env[nuitka.MEMORY_WAIT_ENV] == nuitka.MEMORY_WAIT_PLUGIN
    assert os.path.isfile(nuitka.MEMORY_WAIT_PLUGIN)


def test_a_headless_product_is_rebuilt_with_both(tmp_path, chosen):
    runs = _builder(tmp_path).plan(_stale("AppHeadless"), None)
    assert [r.command[2:] for r in runs] == [["--with-headless"]]


def test_a_separate_headless_option_adds_a_run(tmp_path, chosen):
    runs = _builder(tmp_path, script='"--headless"').plan(_stale("App", "AppHeadless"), None)
    assert [r.command[2:] for r in runs] == [[], ["--headless"]]


def test_an_unknown_product_is_refused(tmp_path, chosen):
    with pytest.raises(SystemExit):
        _builder(tmp_path).plan(_stale("Other"), None)
    assert chosen == []


def test_the_build_args_follow_the_script(tmp_path, chosen, monkeypatch):
    builder = _builder(tmp_path, script="")
    runs = builder.plan(_stale("App"), None)
    calls = recording_run(monkeypatch, base)
    builder.build(runs, ["--jobs=4", "--with-oqs"], "1.0.0", {})
    assert calls[0][0][1:] == [os.path.join(str(tmp_path), "build_exe.py"), "--jobs=4", "--with-oqs"]
    # The app writes its own record.
    assert not os.path.exists(os.path.join(str(tmp_path), ".release"))


def test_executables_are_found_in_dist(tmp_path):
    builder = _builder(tmp_path)
    (tmp_path / "dist").mkdir()
    name = "App.exe" if sys.platform == "win32" else "App"
    exe = tmp_path / "dist" / name
    exe.write_bytes(b"x")
    os.chmod(exe, 0o755)
    (tmp_path / "dist" / "notes.txt").write_text("", encoding="utf-8")
    assert [(a.product, a.kind) for a in builder.artifacts()] == [("App", EXECUTABLE)]


def test_the_version_file_is_the_default(tmp_path):
    builder = _builder(tmp_path)
    (tmp_path / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    assert builder.default_version_source().read() == "1.2.3"


def test_app_version_in_the_module_build_exe_names(tmp_path):
    builder = _builder(tmp_path, script='VERSION_FILE = os.path.join("utils", "version.py")\n')
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "version.py").write_text('APP_VERSION = "0.4.1"\n', encoding="utf-8")
    source = builder.default_version_source()
    assert source.read() == "0.4.1"
    source.write("0.5.0")
    assert (tmp_path / "utils" / "version.py").read_text(encoding="utf-8") == 'APP_VERSION = "0.5.0"\n'


def test_the_lock_file_keeps_its_asset_label(tmp_path):
    builder = _builder(tmp_path)
    lock = tmp_path / "build" / "nuitka" / "build-requirements.lock.txt"
    lock.parent.mkdir(parents=True)
    lock.write_text("", encoding="utf-8")
    assert builder.lock_files() == [(str(lock), "requirements.lock.txt")]


# macOS app bundles

def _dist(tmp_path, monkeypatch, bare_newer=None):
    """dist/Muse.app on macOS, plus a bare dist/Muse when *bare_newer* is given."""
    monkeypatch.setattr(nuitka.sys, "platform", "darwin")
    (tmp_path / "build_exe.py").write_text("", encoding="utf-8")
    bundle = tmp_path / "dist" / "Muse.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    os.utime(bundle, (1000, 1000))
    if bare_newer is not None:
        bare = tmp_path / "dist" / "Muse"
        bare.write_bytes(b"x")
        os.chmod(bare, 0o755)
        os.utime(bare, (2000, 2000) if bare_newer else (500, 500))
    return nuitka.NuitkaBuilder(str(tmp_path), {}, Options())


def test_an_app_bundle_is_found_on_macos(tmp_path, monkeypatch):
    artifacts = _dist(tmp_path, monkeypatch).artifacts()
    assert [(a.product, a.kind, os.path.basename(a.path)) for a in artifacts] == [("Muse", "dir", "Muse.app")]


def test_the_bundle_wins_over_an_older_bare_executable_silently(tmp_path, monkeypatch, caplog):
    artifacts = _dist(tmp_path, monkeypatch, bare_newer=False).artifacts()
    assert [os.path.basename(a.path) for a in artifacts] == ["Muse.app"]
    assert "Both" not in caplog.text


def test_a_newer_bare_executable_beside_the_bundle_is_warned_about(tmp_path, monkeypatch, caplog):
    artifacts = _dist(tmp_path, monkeypatch, bare_newer=True).artifacts()
    assert [os.path.basename(a.path) for a in artifacts] == ["Muse.app"]
    assert "Both" in caplog.text


# Preflight

def _host(monkeypatch, platform, commands=(), xcode=True):
    """Pretend to run on *platform* with only *commands* on PATH."""
    monkeypatch.setattr(nuitka.sys, "platform", platform)
    monkeypatch.setattr(nuitka.shutil, "which", lambda c: f"/usr/bin/{c}" if c in commands else None)
    monkeypatch.setattr(nuitka, "run_version", lambda command: "/Library/Developer" if xcode else None)


def _configured(tmp_path, **settings):
    (tmp_path / "build_exe.py").write_text("", encoding="utf-8")
    return nuitka.NuitkaBuilder(str(tmp_path), {"nuitka": settings}, Options())


def test_preflight_passes_with_a_compiler_and_the_required_commands(tmp_path, monkeypatch):
    _host(monkeypatch, "linux", commands={"gcc", "vlc"})
    _configured(tmp_path, requires=["vlc"]).preflight()


def test_preflight_names_a_missing_required_command(tmp_path, monkeypatch):
    _host(monkeypatch, "linux", commands={"gcc"})
    with pytest.raises(SystemExit) as exited:
        _configured(tmp_path, requires=["vlc"]).preflight()
    assert "vlc" in str(exited.value.code)


def test_preflight_refuses_requires_that_is_not_a_list(tmp_path, monkeypatch):
    _host(monkeypatch, "linux", commands={"gcc", "vlc"})
    with pytest.raises(SystemExit) as exited:
        _configured(tmp_path, requires="vlc").preflight()
    assert "list of command names" in str(exited.value.code)


def test_preflight_needs_a_compiler_on_linux(tmp_path, monkeypatch):
    _host(monkeypatch, "linux")
    with pytest.raises(SystemExit) as exited:
        _configured(tmp_path).preflight()
    assert "C compiler" in str(exited.value.code)


def test_preflight_needs_the_command_line_tools_on_macos(tmp_path, monkeypatch):
    _host(monkeypatch, "darwin", commands={"cc"}, xcode=False)
    with pytest.raises(SystemExit) as exited:
        _configured(tmp_path).preflight()
    assert "xcode-select --install" in str(exited.value.code)


def test_preflight_leaves_the_compiler_to_nuitka_on_windows(tmp_path, monkeypatch):
    _host(monkeypatch, "win32")
    _configured(tmp_path).preflight()


def test_defaults_carry_oqs_and_the_torch_hint():
    assert nuitka.NuitkaBuilder.default_features["oqs"]["rebuild_option"] == "--with-oqs"
    assert nuitka.NuitkaBuilder.default_variant_hints["torch"]["variant"] == "cuda"


# The contract with the apps' build_exe.py

def test_memory_wait_plugin_prints_the_path_alone(tmp_path, monkeypatch, capsys):
    # Works from a folder that is no app checkout.
    monkeypatch.chdir(tmp_path)
    cli.main(["--memory-wait-plugin"])
    assert capsys.readouterr().out == nuitka.MEMORY_WAIT_PLUGIN + "\n"


def test_the_plugin_variable_is_the_one_build_exe_reads():
    assert nuitka.MEMORY_WAIT_ENV == "NUITKA_MEMORY_WAIT_PLUGIN"
