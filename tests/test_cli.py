"""app_release.cli: choosing the backends, and a whole run to staging with
--no-upload (git stubbed, nothing built)."""

import json
import os
import subprocess
import sys

import pytest

from app_release import cli, vcs
from app_release.builders.nuitka import NuitkaBuilder
from app_release.destinations.github import GitHubRelease
from helpers import HEAD, checkout_git, upstream_answers, write_record


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


def _nuitka_app(tmp_path):
    """VERSION, build_exe.py, a build record from HEAD and an executable after it."""
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


def _main(*argv):
    try:
        cli.main(list(argv))
    finally:
        for handler in list(cli.logger.handlers):
            cli.logger.removeHandler(handler)
            handler.close()


def _no_build(*args, **kwargs):
    raise AssertionError("built")


def test_a_nuitka_app_is_staged_as_before(tmp_path, monkeypatch, capsys):
    """A --no-build --no-upload run stages the assets under today's names."""
    monkeypatch.setattr(vcs, "git", checkout_git())
    _nuitka_app(tmp_path)
    _main(str(tmp_path), "--no-build", "--no-upload")
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


def test_an_unreachable_remote_stops_an_upload_before_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git())
    monkeypatch.setattr(cli.pipeline, "rebuild_stale", _no_build)
    _nuitka_app(tmp_path)
    with pytest.raises(SystemExit) as exited:
        _main(str(tmp_path), "--destination", "none")
    assert "Cannot reach the git remote origin" in str(exited.value.code)


def test_a_checkout_behind_its_upstream_stops_before_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git(answers=upstream_answers(behind=2)))
    monkeypatch.setattr(cli.pipeline, "rebuild_stale", _no_build)
    _nuitka_app(tmp_path)
    with pytest.raises(SystemExit) as exited:
        _main(str(tmp_path), "--no-upload")
    assert "origin/main has 2 commit(s)" in str(exited.value.code) and "pull first" in str(exited.value.code)


def test_force_goes_ahead_behind_the_upstream(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git(answers=upstream_answers(behind=2)))
    _nuitka_app(tmp_path)
    _main(str(tmp_path), "--no-build", "--no-upload", "--force")
    assert (tmp_path / "dist" / "release" / "v1.0.0").is_dir()


def test_a_failed_fetch_stops_an_upload_but_not_a_trial(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git(answers=upstream_answers(fetched=False)))
    monkeypatch.setattr(cli.pipeline, "rebuild_stale", _no_build)
    _nuitka_app(tmp_path)
    with pytest.raises(SystemExit) as exited:
        _main(str(tmp_path), "--destination", "none")
    assert "Cannot fetch origin/main" in str(exited.value.code)
    _main(str(tmp_path), "--no-build", "--no-upload")
    assert (tmp_path / "dist" / "release" / "v1.0.0").is_dir()


def test_an_up_to_date_checkout_goes_ahead(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git(answers=upstream_answers(behind=0)))
    _nuitka_app(tmp_path)
    _main(str(tmp_path), "--no-build", "--no-upload")
    assert (tmp_path / "dist" / "release" / "v1.0.0").is_dir()


def _tagged_with_draft(tmp_path, monkeypatch, draft):
    """An app whose current version 1.0.0 is tagged, with GitHub reporting *draft* for it."""
    monkeypatch.setattr(vcs, "git", checkout_git(answers={
        ("rev-parse", "-q", "--verify", "refs/tags/v1.0.0"): HEAD,
        ("rev-parse", "-q", "--verify", "refs/tags/v1.0.0^{commit}"): HEAD}))
    monkeypatch.setattr(cli.pipeline, "rebuild_stale", _no_build)
    monkeypatch.setattr(GitHubRelease, "draft_in_progress", lambda self, tag: draft)
    _nuitka_app(tmp_path)


def test_bump_is_refused_while_the_current_version_is_a_draft(tmp_path, monkeypatch):
    _tagged_with_draft(tmp_path, monkeypatch, draft=True)
    with pytest.raises(SystemExit) as exited:
        _main(str(tmp_path), "--bump", "minor", "--no-upload")
    assert "v1.0.0 is a draft release in progress" in str(exited.value.code)
    assert (tmp_path / "VERSION").read_text(encoding="utf-8") == "1.0.0\n"


def test_an_unknown_draft_state_stops_a_bump_that_will_upload(tmp_path, monkeypatch):
    _tagged_with_draft(tmp_path, monkeypatch, draft=None)
    monkeypatch.setattr(GitHubRelease, "check_ready", lambda self: None)
    with pytest.raises(SystemExit) as exited:
        _main(str(tmp_path), "--bump", "minor")
    assert "Cannot tell whether v1.0.0 is a draft" in str(exited.value.code)
    assert (tmp_path / "VERSION").read_text(encoding="utf-8") == "1.0.0\n"


def test_bump_goes_ahead_when_the_current_version_is_published(tmp_path, monkeypatch):
    bumped = []
    monkeypatch.setattr(cli, "bump", lambda repo, source, version, part, no_build: bumped.append(part) or version)
    _tagged_with_draft(tmp_path, monkeypatch, draft=False)
    _main(str(tmp_path), "--bump", "minor", "--no-build", "--no-upload")
    assert bumped == ["minor"]


def test_status_runs_only_reading_commands(tmp_path, monkeypatch, capsys):
    git_calls, gh_calls = [], []
    answers = {("ls-remote", "origin", "refs/tags/v1.0.0", "refs/tags/v1.0.0^{}"): f"{HEAD}\trefs/tags/v1.0.0"}
    fake = checkout_git(answers=answers)
    monkeypatch.setattr(vcs, "git", lambda repo, *args, **kw: git_calls.append(args) or fake(repo, *args))
    from app_release.destinations import github
    monkeypatch.setattr(github.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(github.subprocess, "run", lambda args, **kw: gh_calls.append(args) or
                        subprocess.CompletedProcess(args, 0, stderr="", stdout=json.dumps(
                            {"isDraft": True, "body": "", "assets": [
                                {"name": "SHA256SUMS-1.0.0-windows-x64.txt", "size": 1}]})))
    monkeypatch.setattr(cli.pipeline, "rebuild_stale", _no_build)
    _nuitka_app(tmp_path)
    (tmp_path / "release.toml").write_text('platforms = ["windows-x64", "linux-x64"]\n', encoding="utf-8")
    _main(str(tmp_path), "--status")
    out = capsys.readouterr().out
    assert "Tag v1.0.0: here absent; on origin at " + HEAD[:10] in out
    assert "is a draft" in out and "missing: linux-x64" in out
    assert {args[0] for args in git_calls} <= {"rev-parse", "ls-remote"}
    assert gh_calls and all(args[:3] == ["gh", "release", "view"] for args in gh_calls)
    assert not (tmp_path / "dist" / "release").exists()


def test_a_missing_gh_stops_an_upload_before_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git())
    monkeypatch.setattr(cli.pipeline, "rebuild_stale", _no_build)
    from app_release.destinations import github
    monkeypatch.setattr(github.shutil, "which", lambda name: None)
    _nuitka_app(tmp_path)
    with pytest.raises(SystemExit) as exited:
        _main(str(tmp_path))
    assert "gh (GitHub CLI) is not installed" in str(exited.value.code)


def test_the_old_command_name_fails_naming_app_release():
    with pytest.raises(SystemExit) as exited:
        cli.renamed(["--memory-wait-plugin"])
    assert "app-release --memory-wait-plugin" in str(exited.value.code)
