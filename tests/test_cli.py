"""app_release.cli: choosing the backends, and a whole run to staging with
--no-upload (git stubbed, nothing built)."""

import os
import sys

import pytest

from app_release import cli, vcs
from app_release.builders.nuitka import NuitkaBuilder
from app_release.destinations.github import GitHubRelease
from helpers import HEAD, checkout_git, write_record


def _opts(*argv):
    return cli._parser().parse_args(list(argv))


def test_no_configuration_means_nuitka_and_github(tmp_path):
    (tmp_path / "build_exe.py").write_text("", encoding="utf-8")
    builder = cli._builder(str(tmp_path), {}, _opts())
    assert isinstance(builder, NuitkaBuilder)
    destinations = cli._destinations(str(tmp_path), {}, _opts())
    assert [type(d) for d in destinations] == [GitHubRelease]


def test_a_folder_without_build_exe_is_refused(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(tmp_path)])
    assert "build_exe.py" in str(excinfo.value)


def test_another_build_system_is_suggested(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "1.0.0"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        cli.main([str(tmp_path)])
    assert 'builder = "cargo"' in str(excinfo.value)


def test_configuration_and_options_choose_backends(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "1.0.0"\n', encoding="utf-8")
    config = {"builder": "cargo", "destinations": ["none"]}
    assert cli._builder(str(tmp_path), config, _opts()).name == "cargo"
    assert [d.name for d in cli._destinations(str(tmp_path), config, _opts())] == ["none"]
    assert [d.name for d in cli._destinations(str(tmp_path), config, _opts("--destination", "github"))] == ["github"]


def test_an_unknown_backend_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        cli._builder(str(tmp_path), {"builder": "maven"}, _opts())
    with pytest.raises(SystemExit):
        cli._destinations(str(tmp_path), {"destinations": ["pypi"]}, _opts())


def test_python_is_an_alias_of_toolchain():
    assert _opts("--python", "/usr/bin/python3.11").toolchain == "/usr/bin/python3.11"


def test_a_nuitka_app_is_staged_as_before(tmp_path, monkeypatch, capsys):
    """VERSION, build_exe.py, a build record from HEAD and an executable after it:
    a --no-build --no-upload run stages the assets under today's names."""
    monkeypatch.setattr(vcs, "git", checkout_git())
    (tmp_path / "build_exe.py").write_text("", encoding="utf-8")
    (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    record_dir = tmp_path / "build" / "nuitka"
    record_dir.mkdir(parents=True)
    record = write_record(record_dir, commit=HEAD, with_oqs=True, python="3.11.9")
    (record_dir / "build-requirements.lock.txt").write_text("x==1\n", encoding="utf-8")
    (tmp_path / "dist").mkdir()
    exe = tmp_path / "dist" / ("App.exe" if sys.platform == "win32" else "App")
    exe.write_bytes(b"x")
    os.chmod(exe, 0o755)
    later = os.path.getmtime(record) + 10
    os.utime(exe, (later, later))
    try:
        cli.main([str(tmp_path), "--no-build", "--no-upload"])
    finally:
        for handler in list(cli.logger.handlers):
            cli.logger.removeHandler(handler)
            handler.close()
    from app_release import platforms
    stage = tmp_path / "dist" / "release" / "v1.0.0"
    suffix = f"1.0.0-{platforms.host()}"
    names = sorted(os.listdir(stage))
    assert f"App-{suffix}{'.exe' if sys.platform == 'win32' else ''}" in names
    assert f"App-{suffix}-requirements.lock.txt" in names
    assert f"SHA256SUMS-{suffix}.txt" in names
    notes = (stage / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    assert "OQS (post-quantum) encryption: included." in notes
    assert "gh release create v1.0.0" in capsys.readouterr().out
    assert (tmp_path / "build" / "logs").is_dir()


def test_the_old_command_name_fails_naming_app_release():
    with pytest.raises(SystemExit) as exited:
        cli.renamed(["--memory-wait-plugin"])
    assert "app-release --memory-wait-plugin" in str(exited.value.code)
