"""The github destination, with gh stubbed."""

import json
import subprocess

import pytest

from app_release.destinations import github, none
from app_release.model import Asset, ReleasePlan
from helpers import HEAD


def _gh(monkeypatch, logged_in=True, release=None, others=None, listed=()):
    """Stubs gh: logged in or not, *release* (the assets of v1.0.0) or no
    release, *others* (tag -> assets) and the *listed* releases.
    Returns the commands run."""
    calls = []
    assets_by_tag = {**(others or {}), "v1.0.0": release}

    def fake_run(args, **kwargs):
        calls.append(args)
        if args == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0 if logged_in else 1, stdout="", stderr="")
        if args[:3] == ["gh", "release", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(list(listed)), stderr="")
        if args[:3] == ["gh", "release", "view"]:
            assets = assets_by_tag.get(args[3])
            if assets is None:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="not found")
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"assets": assets}), stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(github.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    return calls


def _release(tmp_path, publish=False):
    asset = tmp_path / "App-1.0.0-windows-x64.exe"
    asset.write_bytes(b"12345")
    return ReleasePlan(repo=str(tmp_path), tag="v1.0.0", commit=HEAD, remote="origin", title="App 1.0.0",
                       assets=[Asset(asset.name, str(asset))], notes="RELEASE_NOTES.md", publish=publish)


def _view(monkeypatch, returncode=0, stdout="", stderr=""):
    monkeypatch.setattr(github.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(github.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(
        args, returncode, stdout=stdout, stderr=stderr))


@pytest.mark.parametrize("returncode, stdout, stderr, expected", [
    (0, json.dumps({"isDraft": True}), "", True),
    (0, json.dumps({"isDraft": False}), "", False),
    (1, "", "release not found", False),
    (1, "", "HTTP 502: Bad Gateway", None),
])
def test_draft_in_progress(tmp_path, monkeypatch, returncode, stdout, stderr, expected):
    _view(monkeypatch, returncode, stdout, stderr)
    assert github.GitHubRelease(str(tmp_path), {}).draft_in_progress("v1.0.0") is expected


def test_draft_in_progress_is_unknown_without_gh(tmp_path, monkeypatch):
    monkeypatch.setattr(github.shutil, "which", lambda name: None)
    assert github.GitHubRelease(str(tmp_path), {}).draft_in_progress("v1.0.0") is None


def test_no_destination_has_no_draft(tmp_path):
    assert none.NoDestination(str(tmp_path), {}).draft_in_progress("v1.0.0") is False


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


def _listed(tag, draft=False, prerelease=False, immutable=False, latest=False):
    return {"tagName": tag, "isDraft": draft, "isPrerelease": prerelease, "isImmutable": immutable,
            "isLatest": latest}


def _downloads(version):
    return [{"name": f"App-{version}-windows-x64.exe", "size": 9},
            {"name": f"SHA256SUMS-{version}-windows-x64.txt", "size": 1},
            {"name": f"App-{version}-windows-x64-requirements.lock.txt", "size": 1}]


def _delete(tag, name):
    return ["gh", "release", "delete-asset", tag, name, "--yes"]


def test_publishing_prunes_the_downloads_of_older_releases(tmp_path, monkeypatch):
    others = {f"v{v}": _downloads(v) for v in ("0.9.0", "0.8.0", "0.7.0", "0.6.0", "0.5.0", "0.4.0")}
    _gh(monkeypatch, release=[{"name": "App-1.0.0-windows-x64.exe", "size": 5}], others=others,
        listed=[_listed("v1.0.0", draft=True), _listed("v0.9.0", latest=True), _listed("v0.10.0"),
                _listed("v0.8.0"), _listed("v0.7.0", immutable=True), _listed("v0.6.0", prerelease=True),
                _listed("v0.5.0", draft=True), _listed("v0.4.0"), _listed("nightly")])
    destination = github.GitHubRelease(str(tmp_path), {"github": {"keep": 2}})
    steps = destination.plan(_release(tmp_path, publish=True))
    # Kept: v1.0.0 (being published) and v0.10.0 (newest by version); v0.9.0 is the latest.
    assert steps == [["gh", "release", "edit", "v1.0.0", "--draft=false"],
                     _delete("v0.8.0", "App-0.8.0-windows-x64.exe"),
                     _delete("v0.4.0", "App-0.4.0-windows-x64.exe")]
    assert destination.irreversible(steps)


def test_a_new_release_counts_among_those_kept(tmp_path, monkeypatch):
    _gh(monkeypatch, others={"v0.9.0": _downloads("0.9.0")}, listed=[_listed("v0.9.0")])
    steps = github.GitHubRelease(str(tmp_path), {"github": {"keep": 1}}).plan(_release(tmp_path, publish=True))
    assert steps[0][:3] == ["gh", "release", "create"]
    assert steps[2:] == [_delete("v0.9.0", "App-0.9.0-windows-x64.exe")]


def test_a_pruned_release_has_nothing_left_to_delete(tmp_path, monkeypatch):
    _gh(monkeypatch, release=[{"name": "App-1.0.0-windows-x64.exe", "size": 5}],
        others={"v0.9.0": _downloads("0.9.0")[1:]}, listed=[_listed("v1.0.0"), _listed("v0.9.0")])
    destination = github.GitHubRelease(str(tmp_path), {"github": {"keep": 1}})
    steps = destination.plan(_release(tmp_path, publish=True))
    assert steps == [["gh", "release", "edit", "v1.0.0", "--draft=false"]]
    assert not destination.irreversible(steps)


def test_nothing_is_pruned_without_keep_or_publish(tmp_path, monkeypatch):
    calls = _gh(monkeypatch, release=[{"name": "App-1.0.0-windows-x64.exe", "size": 5}],
                others={"v0.9.0": _downloads("0.9.0")}, listed=[_listed("v0.9.0")])
    github.GitHubRelease(str(tmp_path), {}).plan(_release(tmp_path, publish=True))
    assert github.GitHubRelease(str(tmp_path), {"github": {"keep": 1}}).plan(_release(tmp_path)) == []
    assert not any(call[:3] == ["gh", "release", "list"] for call in calls)


def test_keep_must_be_a_whole_number(tmp_path, monkeypatch):
    _gh(monkeypatch, release=[{"name": "App-1.0.0-windows-x64.exe", "size": 5}])
    with pytest.raises(SystemExit) as excinfo:
        github.GitHubRelease(str(tmp_path), {"github": {"keep": "5"}}).plan(_release(tmp_path, publish=True))
    assert "keep" in str(excinfo.value)


def test_github_limits_asset_size():
    assert github.GitHubRelease.max_asset_bytes == 2 * 1024 ** 3


def test_none_publishes_nothing(tmp_path):
    destination = none.NoDestination(str(tmp_path), {})
    assert destination.plan(_release(tmp_path)) == []
    destination.check_ready()
