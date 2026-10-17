"""The github destination, with gh stubbed."""

import json
import subprocess

import pytest

from app_release.destinations import github, none
from app_release.model import Asset, ReleasePlan
from helpers import HEAD


def _gh(monkeypatch, logged_in=True, release=None):
    """Stubs gh: logged in or not, and *release* (its assets) or no release.
    Returns the commands run."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0 if logged_in else 1, stdout="", stderr="")
        if args[:3] == ["gh", "release", "view"]:
            if release is None:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="not found")
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"assets": release}), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(github.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    return calls


def _release(tmp_path, publish=False):
    asset = tmp_path / "App-1.0.0-windows-x64.exe"
    asset.write_bytes(b"12345")
    return ReleasePlan(repo=str(tmp_path), tag="v1.0.0", commit=HEAD, remote="origin", title="App 1.0.0",
                       assets=[Asset(asset.name, str(asset))], notes="RELEASE_NOTES.md", publish=publish)


def test_not_logged_in_stops(tmp_path, monkeypatch):
    calls = _gh(monkeypatch, logged_in=False)
    with pytest.raises(SystemExit) as excinfo:
        github.GitHubRelease(str(tmp_path), {}).check_ready()
    assert "gh auth login" in str(excinfo.value)
    assert calls == [["gh", "auth", "status"]]


def test_a_new_release_is_created_as_a_draft(tmp_path, monkeypatch):
    _gh(monkeypatch)
    destination = github.GitHubRelease(str(tmp_path), {})
    steps = destination.plan(_release(tmp_path))
    assert steps == [["gh", "release", "create", "v1.0.0", "App-1.0.0-windows-x64.exe", "--title", "App 1.0.0",
                      "--notes-file", "RELEASE_NOTES.md", "--draft", "--verify-tag"]]
    assert destination.reads_notes(steps)


def test_only_missing_assets_are_uploaded(tmp_path, monkeypatch):
    _gh(monkeypatch, release=[{"name": "App-1.0.0-windows-x64.exe", "size": 5}])
    destination = github.GitHubRelease(str(tmp_path), {})
    assert destination.plan(_release(tmp_path)) == []
    _gh(monkeypatch, release=[{"name": "App-1.0.0-windows-x64.exe", "size": 4}])
    steps = destination.plan(_release(tmp_path))
    assert steps == [["gh", "release", "upload", "v1.0.0", "App-1.0.0-windows-x64.exe", "--clobber"]]
    assert not destination.reads_notes(steps)


def test_publish_undrafts(tmp_path, monkeypatch):
    _gh(monkeypatch, release=[{"name": "App-1.0.0-windows-x64.exe", "size": 5}])
    steps = github.GitHubRelease(str(tmp_path), {}).plan(_release(tmp_path, publish=True))
    assert steps == [["gh", "release", "edit", "v1.0.0", "--draft=false"]]


def test_github_limits_asset_size():
    assert github.GitHubRelease.max_asset_bytes == 2 * 1024 ** 3


def test_none_publishes_nothing(tmp_path):
    destination = none.NoDestination(str(tmp_path), {})
    assert destination.plan(_release(tmp_path)) == []
    destination.check_ready()
