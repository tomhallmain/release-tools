"""The github destination, with gh stubbed."""

import json
import subprocess

import pytest

from app_release import pipeline, vcs
from app_release.destinations import github, none
from app_release.model import EXECUTABLE, Artifact, Asset, ReleasePlan
from helpers import HEAD


def _gh(monkeypatch, logged_in=True, release=None, others=None, listed=(), bodies=None):
    """Stubs gh: logged in or not, *release* (the assets of v1.0.0) or no
    release, *others* (tag -> assets), the *listed* releases and *bodies*
    (tag -> notes). Returns the commands run."""
    calls = []
    assets_by_tag = {**(others or {}), "v1.0.0": release}
    bodies = bodies or {}

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
            return subprocess.CompletedProcess(args, 0, stderr="", stdout=json.dumps(
                {"assets": assets, "body": bodies.get(args[3], "")}))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(github.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(github.subprocess, "run", fake_run)
    return calls


def _release(tmp_path, publish=False, sums=False, **plan):
    """A plan with App-1.0.0-windows-x64.exe and, with *sums*, its checksums file."""
    names = ["App-1.0.0-windows-x64.exe"] + (["SHA256SUMS-1.0.0-windows-x64.txt"] if sums else [])
    assets = []
    for name in names:
        (tmp_path / name).write_bytes(b"12345")
        assets.append(Asset(name, str(tmp_path / name)))
    return ReleasePlan(repo=str(tmp_path), tag="v1.0.0", commit=HEAD, remote="origin", title="App 1.0.0",
                       assets=assets, notes="RELEASE_NOTES.md", publish=publish, **plan)


def _sums(platform):
    return {"name": f"SHA256SUMS-1.0.0-{platform}.txt", "size": 1}


EXE = {"name": "App-1.0.0-windows-x64.exe", "size": 5}
ALL = ["windows-x64", "macos-arm64", "linux-x64"]


def test_publish_is_refused_while_a_configured_platform_is_missing(tmp_path, monkeypatch):
    _gh(monkeypatch, release=[EXE, _sums("macos-arm64")])
    with pytest.raises(SystemExit) as exited:
        github.GitHubRelease(str(tmp_path), {}).plan(_release(tmp_path, publish=True, sums=True, platforms=ALL))
    assert str(exited.value.code).startswith("v1.0.0 has no build for linux-x64 yet")


def test_publish_counts_earlier_uploads_and_this_run(tmp_path, monkeypatch):
    _gh(monkeypatch, release=[EXE, _sums("macos-arm64-cuda"), _sums("linux-x64")])
    steps = github.GitHubRelease(str(tmp_path), {}).plan(_release(tmp_path, publish=True, sums=True, platforms=ALL))
    assert steps[-1] == ["gh", "release", "edit", "v1.0.0", "--draft=false"]


def test_force_publishes_without_a_platform(tmp_path, monkeypatch, caplog):
    _gh(monkeypatch, release=[EXE])
    steps = github.GitHubRelease(str(tmp_path), {}).plan(
        _release(tmp_path, publish=True, sums=True, platforms=ALL, force=True))
    assert steps[-1] == ["gh", "release", "edit", "v1.0.0", "--draft=false"]
    assert "without a build for macos-arm64, linux-x64" in caplog.text


def test_status_names_the_platforms_built_and_missing(tmp_path, monkeypatch):
    _gh(monkeypatch, release=[EXE, _sums("windows-x64")])
    lines = github.GitHubRelease(str(tmp_path), {}).status_lines("v1.0.0", ALL)
    assert "Platforms built: windows-x64." in lines[1]
    assert "missing: macos-arm64, linux-x64." in lines[2]


# The notes merge, the retag guard and the per-platform commit check

MAC_EXE = {"name": "App-1.0.0-macos-arm64", "size": 5}
OLD = "c" * 40


def _notes_for(tmp_path, monkeypatch, platform, name, commit=HEAD):
    """The notes a run on *platform* generates for executable *name*."""
    monkeypatch.setattr(vcs, "git", lambda repo, *args, **kwargs: None)
    exe = tmp_path / name
    exe.write_bytes(b"12345")
    return pipeline.release_notes(str(tmp_path), "App 1.0.0", None, commit,
                                  [Artifact("App", str(exe), EXECUTABLE, platform)], [Asset(name, str(exe))])


def _windows_run_on_mac_draft(tmp_path, monkeypatch, mac_commit=HEAD, **plan):
    """A Windows run whose draft holds the macOS build and its notes, with a
    hand-added line; returns (destination, plan)."""
    body = _notes_for(tmp_path, monkeypatch, "macos-arm64", MAC_EXE["name"], mac_commit)
    body = body.replace("## Notes\n\n", "## Notes\n\n- Added on GitHub.\n")
    (tmp_path / "RELEASE_NOTES.md").write_text(
        _notes_for(tmp_path, monkeypatch, "windows-x64", EXE["name"]), encoding="utf-8")
    _gh(monkeypatch, release=[MAC_EXE, _sums("macos-arm64")], bodies={"v1.0.0": body})
    return github.GitHubRelease(str(tmp_path), {}), _release(tmp_path, sums=True, **plan)


def test_a_first_run_creates_the_notes_with_its_part_and_downloads(tmp_path, monkeypatch):
    notes = _notes_for(tmp_path, monkeypatch, "windows-x64", EXE["name"])
    assert f"<!-- app-release:platform windows-x64 commit={HEAD} -->" in notes
    assert "- `App-1.0.0-windows-x64.exe` (0 MB)" in notes and "SmartScreen" in notes


def test_a_second_platform_merges_its_part_and_keeps_hand_edits(tmp_path, monkeypatch):
    destination, release = _windows_run_on_mac_draft(tmp_path, monkeypatch)
    steps = destination.plan(release)
    assert steps[-1] == ["gh", "release", "edit", "v1.0.0", "--notes-file", "RELEASE_NOTES.merged.md"]
    assert destination.reads_notes(steps)
    merged = (tmp_path / "RELEASE_NOTES.merged.md").read_text(encoding="utf-8")
    assert "app-release:platform macos-arm64" in merged and "app-release:platform windows-x64" in merged
    assert "`App-1.0.0-macos-arm64`" in merged and "`App-1.0.0-windows-x64.exe`" in merged
    assert "SHA256SUMS" not in merged and "- Added on GitHub." in merged
    assert merged.index("platform macos-arm64") < merged.index("platform windows-x64") < merged.index("## Notes")


def test_a_rerun_replaces_its_part_and_unchanged_notes_need_no_edit(tmp_path, monkeypatch):
    destination, release = _windows_run_on_mac_draft(tmp_path, monkeypatch)
    destination.plan(release)
    merged = (tmp_path / "RELEASE_NOTES.merged.md").read_text(encoding="utf-8")
    _gh(monkeypatch, release=[MAC_EXE, _sums("macos-arm64"), EXE, {**_sums("windows-x64"), "size": 5}],
        bodies={"v1.0.0": merged.replace("\n", "\r\n")})
    assert destination.plan(release) == []
    assert merged.count("app-release:platform windows-x64") == 1


def test_notes_without_parts_and_notes_files_are_left_alone(tmp_path, monkeypatch):
    destination, release = _windows_run_on_mac_draft(tmp_path, monkeypatch, merge_notes=False)
    assert not any("--notes-file" in step for step in destination.plan(release))
    _gh(monkeypatch, release=[MAC_EXE], bodies={"v1.0.0": "Written by hand.\n"})
    assert not any("--notes-file" in step for step in destination.plan(_release(tmp_path, sums=True)))


def test_publish_is_refused_while_a_platform_part_names_another_commit(tmp_path, monkeypatch):
    destination, release = _windows_run_on_mac_draft(tmp_path, monkeypatch, mac_commit=OLD, publish=True)
    with pytest.raises(SystemExit) as exited:
        destination.plan(release)
    assert f"the notes say macos-arm64 from {OLD[:10]}" in str(exited.value.code)


def test_force_publishes_with_a_stale_platform_part(tmp_path, monkeypatch, caplog):
    destination, release = _windows_run_on_mac_draft(tmp_path, monkeypatch, mac_commit=OLD, publish=True, force=True)
    assert destination.plan(release)[-1] == ["gh", "release", "edit", "v1.0.0", "--draft=false"]
    assert "publishing anyway" in caplog.text


def test_retag_is_refused_under_another_platforms_build(tmp_path, monkeypatch):
    destination, release = _windows_run_on_mac_draft(tmp_path, monkeypatch, retag=True, remote_tag=OLD)
    with pytest.raises(SystemExit) as exited:
        destination.plan(release)
    assert str(exited.value.code).startswith("Moving v1.0.0 leaves the macos-arm64 build in the draft")


def test_retag_goes_ahead_with_force_or_without_other_platforms(tmp_path, monkeypatch, caplog):
    destination, release = _windows_run_on_mac_draft(tmp_path, monkeypatch, retag=True, remote_tag=OLD, force=True)
    destination.plan(release)
    assert "going ahead (--force)" in caplog.text
    _gh(monkeypatch, release=[EXE, _sums("windows-x64")])
    github.GitHubRelease(str(tmp_path), {}).plan(_release(tmp_path, sums=True, retag=True, remote_tag=OLD))


def test_status_names_the_notes_parts(tmp_path, monkeypatch):
    body = _notes_for(tmp_path, monkeypatch, "macos-arm64", MAC_EXE["name"])
    _gh(monkeypatch, release=[MAC_EXE, _sums("macos-arm64")], bodies={"v1.0.0": body})
    lines = github.GitHubRelease(str(tmp_path), {}).status_lines("v1.0.0", ["macos-arm64", "linux-x64"])
    assert f"  Notes parts: macos-arm64 (built from {HEAD[:10]})." in lines
    assert "  Configured platforms without a notes part: linux-x64." in lines


def test_status_without_gh_or_a_release(tmp_path, monkeypatch):
    _view(monkeypatch, 1, stderr="release not found")
    assert github.GitHubRelease(str(tmp_path), {}).status_lines("v1.0.0", []) == ["GitHub: no release for v1.0.0 yet."]
    monkeypatch.setattr(github.shutil, "which", lambda name: None)
    assert "not installed" in github.GitHubRelease(str(tmp_path), {}).status_lines("v1.0.0", [])[0]


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
