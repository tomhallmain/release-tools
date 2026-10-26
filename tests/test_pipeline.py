"""app_release.pipeline; git is stubbed, so nothing is built, tagged or uploaded."""

import builtins
import os
import subprocess

import pytest

from app_release import __version__, pipeline, vcs
from app_release.model import DIRECTORY, EXECUTABLE, PACKAGE, Artifact
from helpers import BUILT, HEAD, TREE, built, checkout_git, fake_git


# write_generated_notes

def test_generated_notes_are_written_with_a_stamp(tmp_path):
    notes = tmp_path / "RELEASE_NOTES.md"
    assert pipeline.write_generated_notes(str(notes), "first\n") is True
    assert notes.read_text(encoding="utf-8") == "first\n"
    assert (tmp_path / "RELEASE_NOTES.md.generated").is_file()


def test_unedited_notes_are_regenerated(tmp_path):
    notes = tmp_path / "RELEASE_NOTES.md"
    pipeline.write_generated_notes(str(notes), "first\n")
    assert pipeline.write_generated_notes(str(notes), "second\n") is True
    assert notes.read_text(encoding="utf-8") == "second\n"


def test_edited_notes_are_kept(tmp_path):
    notes = tmp_path / "RELEASE_NOTES.md"
    pipeline.write_generated_notes(str(notes), "first\n")
    notes.write_text("edited by hand\n", encoding="utf-8")
    assert pipeline.write_generated_notes(str(notes), "second\n") is False
    assert notes.read_text(encoding="utf-8") == "edited by hand\n"


def test_notes_without_a_stamp_are_regenerated(tmp_path):
    notes = tmp_path / "RELEASE_NOTES.md"
    notes.write_text("from an earlier run\n", encoding="utf-8")
    assert pipeline.write_generated_notes(str(notes), "fresh\n") is True
    assert notes.read_text(encoding="utf-8") == "fresh\n"


# approve_notes

@pytest.fixture
def notes_file(tmp_path):
    path = tmp_path / "RELEASE_NOTES.md"
    path.write_text("# App 1.0.0\n", encoding="utf-8")
    return str(path)


def _answers(monkeypatch, *answers):
    replies = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(replies))


def test_approving_the_notes_returns(notes_file, monkeypatch, capsys):
    _answers(monkeypatch, "a")
    pipeline.approve_notes(notes_file)
    assert "# App 1.0.0" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "n", "q"])
def test_anything_but_approve_or_edit_aborts(notes_file, monkeypatch, answer):
    _answers(monkeypatch, answer)
    with pytest.raises(SystemExit):
        pipeline.approve_notes(notes_file)


def test_edit_opens_the_editor_then_asks_again(notes_file, monkeypatch, capsys):
    edited = []

    def fake_edit(path):
        edited.append(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Edited notes\n")

    monkeypatch.setattr(pipeline, "edit", fake_edit)
    _answers(monkeypatch, "e", "a")
    pipeline.approve_notes(notes_file)
    assert edited == [notes_file]
    assert "# Edited notes" in capsys.readouterr().out


# behind

def _behind(tmp_path, record, artifact, head=HEAD, head_tree=TREE):
    return pipeline.behind(str(tmp_path), artifact, "1.0.0", record, head, head_tree, head_time=0)


def test_a_build_from_head_is_current(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD)
    assert _behind(tmp_path, record, artifact) is None


def test_an_ancestor_with_the_same_tree_is_current(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(ancestors={BUILT}))
    record, artifact = built(tmp_path)
    assert _behind(tmp_path, record, artifact) is None


def test_a_rewritten_commit_with_the_same_tree_is_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path)
    assert "cannot be tagged" in _behind(tmp_path, record, artifact)


def test_a_different_tree_is_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(ancestors={BUILT}))
    record, artifact = built(tmp_path)
    reason = _behind(tmp_path, record, artifact, head_tree="u" * 40)
    assert BUILT[:10] in reason and HEAD[:10] in reason


def test_a_build_without_a_recorded_tree_is_judged_by_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(ancestors={BUILT}))
    record, artifact = built(tmp_path, tree=None)
    assert BUILT[:10] in _behind(tmp_path, record, artifact)


def test_a_build_with_uncommitted_changes_is_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD, changes="f" * 40)
    assert "uncommitted" in _behind(tmp_path, record, artifact)


def test_an_artifact_older_than_the_record_is_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD)
    os.utime(artifact.path, (record.written_at - 10, record.written_at - 10))
    assert "predates" in _behind(tmp_path, record, artifact)


def test_without_a_record_an_artifact_older_than_head_is_behind(tmp_path):
    _, artifact = built(tmp_path)
    os.utime(artifact.path, (100, 100))
    assert "older than" in pipeline.behind(str(tmp_path), artifact, "1.0.0", None, HEAD, TREE, 200)
    assert pipeline.behind(str(tmp_path), artifact, "1.0.0", None, HEAD, TREE, 50) is None


# check

def _check(tmp_path, record, artifact, **kwargs):
    return pipeline.check(str(tmp_path), "1.0.0", "v1.0.0", record, [artifact], None, {}, None, **kwargs)


def test_check_accepts_an_ancestor_with_the_same_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(ancestors={BUILT}))
    record, artifact = built(tmp_path)
    errors, _ = _check(tmp_path, record, artifact)
    assert errors == []


def test_check_refuses_a_rewritten_commit_with_the_same_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path)
    errors, _ = _check(tmp_path, record, artifact)
    assert len(errors) == 1
    assert "cannot be tagged" in errors[0]


def test_check_refuses_a_build_from_a_different_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(tree="u" * 40, ancestors={BUILT}))
    record, artifact = built(tmp_path)
    errors, _ = _check(tmp_path, record, artifact)
    assert len(errors) == 1
    assert HEAD[:10] in errors[0] and BUILT[:10] in errors[0]


def test_no_build_only_warns_about_an_older_ancestor(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(tree="u" * 40, ancestors={BUILT}))
    record, artifact = built(tmp_path)
    errors, warnings = _check(tmp_path, record, artifact, no_build=True)
    assert errors == [] and any("--no-build" in w for w in warnings)


def test_check_refuses_a_tag_on_another_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(tag="c" * 40))
    record, artifact = built(tmp_path, commit=HEAD)
    errors, _ = _check(tmp_path, record, artifact)
    assert len(errors) == 1
    assert "v1.0.0" in errors[0]


def test_retag_turns_the_tag_error_into_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(tag="c" * 40))
    record, artifact = built(tmp_path, commit=HEAD)
    errors, warnings = _check(tmp_path, record, artifact, retag=True)
    assert errors == [] and any("--retag" in w for w in warnings)


def test_check_refuses_a_remote_tag_on_another_commit_without_a_local_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD)
    errors, _ = _check(tmp_path, record, artifact, remote_tag="c" * 40)
    assert len(errors) == 1
    assert "on origin" in errors[0] and "c" * 10 in errors[0] and HEAD[:10] in errors[0]


def test_check_refuses_a_remote_tag_on_another_commit_despite_the_local_tag(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(tag=HEAD))
    record, artifact = built(tmp_path, commit=HEAD)
    errors, _ = _check(tmp_path, record, artifact, remote_tag="c" * 40)
    assert len(errors) == 1 and "on origin" in errors[0]


def test_both_tags_on_the_same_other_commit_give_one_error(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(tag="c" * 40))
    record, artifact = built(tmp_path, commit=HEAD)
    errors, _ = _check(tmp_path, record, artifact, remote_tag="c" * 40)
    assert len(errors) == 1


def test_check_accepts_a_remote_tag_on_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD)
    errors, _ = _check(tmp_path, record, artifact, remote_tag=HEAD)
    assert errors == []


def test_a_stale_local_tag_only_warns_when_the_remote_tag_is_on_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git(tag="c" * 40))
    record, artifact = built(tmp_path, commit=HEAD)
    errors, warnings = _check(tmp_path, record, artifact, remote_tag=HEAD)
    assert errors == [] and any("to match" in w for w in warnings)


def test_retag_turns_the_remote_tag_error_into_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD)
    errors, warnings = _check(tmp_path, record, artifact, retag=True, remote_tag="c" * 40)
    assert errors == [] and any("on origin" in w and "--retag" in w for w in warnings)


def test_check_warns_about_an_embedded_checkout_path(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD)
    record.embedded_paths = ["C:\\repos\\App"]
    _, warnings = _check(tmp_path, record, artifact)
    assert any("C:\\repos\\App" in w for w in warnings)


def test_a_variant_hint_suggests_a_variant(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD, packages={"torch": "2.4.0+cu121"})
    hints = {"torch": {"contains": "+cu", "variant": "cuda"}}
    _, warnings = pipeline.check(str(tmp_path), "1.0.0", "v1.0.0", record, [artifact], None, hints, None)
    assert any("--variant cuda" in w for w in warnings)
    _, warnings = pipeline.check(str(tmp_path), "1.0.0", "v1.0.0", record, [artifact], "cuda", hints, None)
    assert not any("--variant" in w for w in warnings)


def test_an_asset_over_the_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, artifact = built(tmp_path, commit=HEAD)
    errors, _ = pipeline.check(str(tmp_path), "1.0.0", "v1.0.0", record, [artifact], None, {}, 2)
    assert any("larger" in e for e in errors)


# feature_args

def test_a_recorded_feature_adds_its_rebuild_option(tmp_path):
    record, _ = built(tmp_path, with_oqs=True)
    features = {"oqs": {"rebuild_option": "--with-oqs"}}
    assert pipeline.feature_args(record, features, ["--jobs=4"]) == ["--jobs=4", "--with-oqs"]
    assert pipeline.feature_args(record, features, ["--with-oqs"]) == ["--with-oqs"]


def test_an_absent_feature_adds_nothing(tmp_path):
    record, _ = built(tmp_path, with_oqs=False)
    assert pipeline.feature_args(record, {"oqs": {"rebuild_option": "--with-oqs"}}, []) == []
    assert pipeline.feature_args(None, {"oqs": {"rebuild_option": "--with-oqs"}}, []) == []


# rebuild_stale

class _Builder:
    """Records what rebuild_stale asks of a builder."""

    def __init__(self, ready=True):
        self.ready = ready
        self.preflighted = False
        self.planned = None
        self.built = None

    def preflight(self):
        self.preflighted = True
        if not self.ready:
            raise SystemExit("not ready")

    def plan(self, stale, record):
        self.planned = [a.product for a in stale]
        return ["run"]

    def build(self, runs, extra_args, version, features):
        self.built = (runs, extra_args, version, features)


def test_rebuild_builds_the_stale_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git(tree="u" * 40))
    record, artifact = built(tmp_path)
    builder = _Builder()
    features = {"oqs": {"rebuild_option": "--with-oqs"}}
    assert pipeline.rebuild_stale(str(tmp_path), "1.0.0", [artifact], builder, record,
                                  ["--with-oqs"], features) is True
    assert builder.planned == ["App"]
    assert builder.built == (["run"], ["--with-oqs"], "1.0.0", {"oqs": True})


def test_nothing_is_rebuilt_when_current(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git())
    record, artifact = built(tmp_path, commit=HEAD)
    builder = _Builder()
    assert pipeline.rebuild_stale(str(tmp_path), "1.0.0", [artifact], builder, record, [], {}) is False
    assert builder.planned is None and not builder.preflighted


def test_a_failed_preflight_stops_before_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git(tree="u" * 40))
    record, artifact = built(tmp_path)
    builder = _Builder(ready=False)
    with pytest.raises(SystemExit):
        pipeline.rebuild_stale(str(tmp_path), "1.0.0", [artifact], builder, record, [], {})
    assert builder.preflighted and builder.planned is None and builder.built is None


def test_nothing_is_rebuilt_from_a_dirty_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", checkout_git(tree="u" * 40, dirty=True))
    record, artifact = built(tmp_path)
    builder = _Builder()
    with pytest.raises(SystemExit):
        pipeline.rebuild_stale(str(tmp_path), "1.0.0", [artifact], builder, record, [], {})
    assert builder.planned is None


# stage

def test_one_platform_is_staged_under_release_names(tmp_path):
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"gui")
    headless = tmp_path / "AppHeadless.exe"
    headless.write_bytes(b"headless")
    lock = tmp_path / "lock.txt"
    lock.write_text("x==1\n", encoding="utf-8")
    artifacts = [Artifact("App", str(exe), EXECUTABLE, "windows-x64"),
                 Artifact("AppHeadless", str(headless), EXECUTABLE, "windows-x64")]
    out = tmp_path / "out"
    downloads, sums = pipeline.stage(str(out), "App", "1.0.0", None, artifacts,
                                     [(str(lock), "requirements.lock.txt")])
    assert [a.name for a in downloads] == ["App-1.0.0-windows-x64.exe", "AppHeadless-1.0.0-windows-x64.exe",
                                          "App-1.0.0-windows-x64-requirements.lock.txt"]
    assert [a.name for a in sums] == ["SHA256SUMS-1.0.0-windows-x64.txt"]
    listed = (out / "SHA256SUMS-1.0.0-windows-x64.txt").read_text(encoding="utf-8")
    assert all(a.name in listed for a in downloads)


def test_several_platforms_get_a_checksums_file_each(tmp_path):
    linux = tmp_path / "tool"
    linux.write_bytes(b"elf")
    windows = tmp_path / "tool.exe"
    windows.write_bytes(b"pe")
    gem = tmp_path / "tool-1.0.0.gem"
    gem.write_bytes(b"gem")
    lock = tmp_path / "Cargo.lock"
    lock.write_text("", encoding="utf-8")
    artifacts = [Artifact("tool", str(linux), EXECUTABLE, "linux-x64"),
                 Artifact("tool", str(windows), EXECUTABLE, "windows-x64"),
                 Artifact("toolgem", str(gem), PACKAGE, None)]
    downloads, sums = pipeline.stage(str(tmp_path / "out"), "tool", "1.0.0", "cuda", artifacts,
                                     [(str(lock), "Cargo.lock")])
    names = [a.name for a in downloads]
    assert "tool-1.0.0-linux-x64-cuda.tar.gz" in names
    assert "tool-1.0.0-windows-x64-cuda.exe" in names
    assert "toolgem-1.0.0-cuda.gem" in names
    # The lock file of a build for several platforms is named for none.
    assert "tool-1.0.0-cuda-Cargo.lock" in names
    assert sorted(a.name for a in sums) == ["SHA256SUMS-1.0.0-cuda.txt", "SHA256SUMS-1.0.0-linux-x64-cuda.txt",
                                            "SHA256SUMS-1.0.0-windows-x64-cuda.txt"]


def test_a_directory_is_zipped(tmp_path):
    import zipfile

    folder = tmp_path / "App"
    folder.mkdir()
    (folder / "app.bin").write_bytes(b"x")
    downloads, _ = pipeline.stage(str(tmp_path / "out"), "App", "1.0.0", None,
                                  [Artifact("App", str(folder), DIRECTORY, "linux-x64")], [])
    assert downloads[0].name == "App-1.0.0-linux-x64.zip"
    with zipfile.ZipFile(downloads[0].path) as archive:
        assert "App/app.bin" in archive.namelist()


def test_a_unix_executable_is_staged_as_an_executable_tar_gz(tmp_path):
    import tarfile

    tool = tmp_path / "tool"
    tool.write_bytes(b"elf")
    os.chmod(tool, 0o600)
    downloads, sums = pipeline.stage(str(tmp_path / "out"), "tool", "1.0.0", None,
                                     [Artifact("tool", str(tool), EXECUTABLE, "linux-x64")], [])
    assert downloads[0].name == "tool-1.0.0-linux-x64.tar.gz"
    with tarfile.open(downloads[0].path) as archive:
        member = archive.getmember("tool")
        assert member.mode == 0o755 and archive.extractfile(member).read() == b"elf"
    assert "tool-1.0.0-linux-x64.tar.gz" in (tmp_path / "out" / sums[0].name).read_text(encoding="utf-8")


def test_unix_executables_stay_bare_when_archiving_is_off(tmp_path):
    tool = tmp_path / "tool"
    tool.write_bytes(b"elf")
    downloads, _ = pipeline.stage(str(tmp_path / "out"), "tool", "1.0.0", None,
                                  [Artifact("tool", str(tool), EXECUTABLE, "macos-arm64")], [],
                                  archive_unix=False)
    assert downloads[0].name == "tool-1.0.0-macos-arm64"


def test_an_app_bundle_is_zipped_with_ditto(tmp_path, monkeypatch):
    bundle = tmp_path / "Muse.app"
    (bundle / "Contents").mkdir(parents=True)
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    target = tmp_path / "out" / "Muse-1.0.0-macos-arm64.zip"

    def fake_ditto(args, **kwargs):
        calls.append(args)
        target.write_bytes(b"zip")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(pipeline.subprocess, "run", fake_ditto)
    downloads, _ = pipeline.stage(str(tmp_path / "out"), "Muse", "1.0.0", None,
                                  [Artifact("Muse", str(bundle), DIRECTORY, "macos-arm64")], [])
    assert downloads[0].name == "Muse-1.0.0-macos-arm64.zip"
    assert calls == [["ditto", "-c", "-k", "--keepParent", str(bundle), str(target)]]


def test_an_app_bundle_without_ditto_is_refused(tmp_path, monkeypatch):
    bundle = tmp_path / "Muse.app"
    bundle.mkdir()
    monkeypatch.setattr(pipeline.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exited:
        pipeline.stage(str(tmp_path / "out"), "Muse", "1.0.0", None,
                       [Artifact("Muse", str(bundle), DIRECTORY, "macos-arm64")], [])
    assert "ditto" in str(exited.value.code)


def test_staged_assets_over_the_limit_are_reported(tmp_path):
    from app_release.model import Asset
    small, large = tmp_path / "small", tmp_path / "large"
    small.write_bytes(b"x")
    large.write_bytes(b"x" * 10)
    errors = pipeline.staged_too_large([Asset("small", str(small)), Asset("large", str(large))], 5)
    assert len(errors) == 1 and errors[0].startswith("large is")
    assert pipeline.staged_too_large([Asset("large", str(large))], None) == []


def test_a_large_folder_is_flagged_before_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", fake_git())
    record, _ = built(tmp_path, commit=HEAD)
    folder = tmp_path / "Muse.app"
    folder.mkdir()
    (folder / "big").write_bytes(b"x" * 10)
    os.utime(folder, (record.written_at + 10, record.written_at + 10))
    errors, warnings = pipeline.check(str(tmp_path), "1.0.0", "v1.0.0", record,
                                      [Artifact("Muse", str(folder), DIRECTORY, "macos-arm64")], None, {}, 5)
    assert errors == [] and any("checked against the asset limit" in w for w in warnings)


def test_two_artifacts_under_one_name_are_refused(tmp_path):
    first = tmp_path / "a" / "tool"
    second = tmp_path / "b" / "tool"
    for path in (first, second):
        path.parent.mkdir()
        path.write_bytes(b"x")
    with pytest.raises(SystemExit):
        pipeline.stage(str(tmp_path / "out"), "tool", "1.0.0", None,
                       [Artifact("tool", str(first), EXECUTABLE, "linux-x64"),
                        Artifact("tool", str(second), EXECUTABLE, "linux-x64")], [])


# platforms in a release

def test_checksum_files_name_the_platforms_built():
    names = ["App-1.0.0-windows-x64.exe", "SHA256SUMS-1.0.0-windows-x64.txt",
             "SHA256SUMS-1.0.0-linux-x64-cuda.txt", "SHA256SUMS-0.9.0-macos-arm64.txt"]
    assert pipeline.checksum_platforms(names, "1.0.0") == {"windows-x64", "linux-x64-cuda"}
    assert pipeline.missing_platforms(names, "1.0.0", ["windows-x64", "linux-x64", "macos-arm64"]) == [
        "macos-arm64"]
    assert pipeline.missing_platforms(names, "1.0.0", []) == []


# release notes

def _notes(tmp_path, monkeypatch, record=None, artifacts=None, signed=False, glibc="", host="windows-x64",
           **kwargs):
    monkeypatch.setattr(vcs, "git", lambda repo, *args: None)
    monkeypatch.setattr(pipeline, "developer_id_signed", lambda path: signed)
    monkeypatch.setattr(pipeline.platforms, "glibc_version", lambda: glibc)
    monkeypatch.setattr(pipeline.platforms, "host", lambda: host)
    exe = tmp_path / "App.exe"
    exe.write_bytes(b"x")
    artifacts = artifacts or [Artifact("App", str(exe), EXECUTABLE, "windows-x64")]
    return pipeline.release_notes(str(tmp_path), "App 1.0.0", record, HEAD, artifacts, [], **kwargs)


def test_release_notes_name_the_tool_version(tmp_path, monkeypatch):
    assert f"app-release {__version__}" in _notes(tmp_path, monkeypatch)


def test_release_notes_name_toolchain_and_dependencies(tmp_path, monkeypatch):
    record, _ = built(tmp_path, python="3.11.9", packages={"torch": "2.4.0"})
    assert "Python 3.11.9, torch 2.4.0." in _notes(tmp_path, monkeypatch, record)


def test_release_notes_say_whether_a_feature_is_included(tmp_path, monkeypatch):
    features = {"oqs": {"rebuild_option": "--with-oqs", "note": "OQS (post-quantum) encryption"}}
    record, _ = built(tmp_path, with_oqs=False)
    assert "- OQS (post-quantum) encryption: not included." in _notes(tmp_path, monkeypatch, record,
                                                                      features=features)


def test_the_smartscreen_line_is_for_windows_executables_only(tmp_path, monkeypatch):
    assert "SmartScreen" in _notes(tmp_path, monkeypatch)
    linux = tmp_path / "tool"
    linux.write_bytes(b"x")
    notes = _notes(tmp_path, monkeypatch, artifacts=[Artifact("tool", str(linux), EXECUTABLE, "linux-x64")])
    assert "SmartScreen" not in notes
    assert "SmartScreen" not in _notes(tmp_path, monkeypatch, notes_config={"unsigned_windows": False})


def _part(notes, platform):
    start = notes.index(f"<!-- app-release:platform {platform} ")
    return notes[start:notes.index("<!-- /app-release:platform -->", start)]


def _mac_and_linux(tmp_path):
    bundle = tmp_path / "App.app"
    bundle.mkdir(exist_ok=True)
    tool = tmp_path / "App"
    tool.write_bytes(b"x")
    return [Artifact("App", str(bundle), DIRECTORY, "macos-arm64"), Artifact("App", str(tool), EXECUTABLE, "linux-x64")]


def test_the_gatekeeper_line_is_in_the_macos_part_of_an_unsigned_build(tmp_path, monkeypatch):
    notes = _notes(tmp_path, monkeypatch, artifacts=_mac_and_linux(tmp_path))
    assert "Open Anyway" in _part(notes, "macos-arm64") and "Open Anyway" not in _part(notes, "linux-x64")
    assert "Open Anyway" not in _notes(tmp_path, monkeypatch, artifacts=_mac_and_linux(tmp_path), signed=True)
    assert "Open Anyway" not in _notes(tmp_path, monkeypatch, artifacts=_mac_and_linux(tmp_path),
                                       notes_config={"unsigned_macos": False})


def test_the_glibc_line_is_for_a_linux_build_made_on_this_machine(tmp_path, monkeypatch):
    notes = _notes(tmp_path, monkeypatch, artifacts=_mac_and_linux(tmp_path), glibc="2.35", host="linux-x64")
    assert "- Needs glibc 2.35 or newer." in _part(notes, "linux-x64")
    assert "glibc" not in _part(notes, "macos-arm64")
    assert "glibc" not in _notes(tmp_path, monkeypatch, artifacts=_mac_and_linux(tmp_path), glibc="2.35",
                                 host="macos-arm64")
    assert "glibc" not in _notes(tmp_path, monkeypatch, artifacts=_mac_and_linux(tmp_path), glibc="2.35",
                                 host="linux-x64", notes_config={"linux_glibc": False})


def test_developer_id_signed_reads_codesign(monkeypatch):
    def codesign(stderr, returncode=0):
        monkeypatch.setattr(pipeline.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(
            args, returncode, stdout="", stderr=stderr))

    codesign("Authority=Developer ID Application: Tom (ABC)\nAuthority=Apple Root CA\n")
    assert pipeline.developer_id_signed("App.app")
    codesign("Signature=adhoc\n")
    assert not pipeline.developer_id_signed("App.app")
    codesign("App: code object is not signed at all\n", returncode=1)
    assert not pipeline.developer_id_signed("App.app")


def test_extra_and_builder_lines_are_added(tmp_path, monkeypatch):
    notes = _notes(tmp_path, monkeypatch, builder_lines=["- From the builder."],
                   notes_config={"extra": ["- Configured."]})
    assert "- From the builder." in notes and "- Configured." in notes


# publish_steps

class _Destination:
    name = "fake"
    reversible = True

    def __init__(self, steps, reads=False, ready=True, calls=None):
        self.steps, self.reads, self.ready, self.calls = steps, reads, ready, calls

    def check_ready(self):
        self.calls.append("check")
        if not self.ready:
            raise SystemExit("not logged in")

    def plan(self, release):
        self.calls.append("plan")
        return self.steps

    def reads_notes(self, steps):
        return self.reads

    def irreversible(self, steps):
        return bool(steps) and not self.reversible


def _release(tmp_path):
    from app_release.model import ReleasePlan
    return ReleasePlan(repo=str(tmp_path), tag="v1.0.0", commit=HEAD, remote="origin", title="App 1.0.0",
                       assets=[], notes="RELEASE_NOTES.md")


def test_destinations_are_checked_before_anything_is_tagged(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vcs, "tag_steps", lambda *a, **k: calls.append("tag") or [])
    with pytest.raises(SystemExit):
        pipeline.publish_steps(_release(tmp_path), [_Destination([], ready=False, calls=calls)])
    assert calls == ["check"]


def test_tag_steps_come_before_the_destinations(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vcs, "tag_steps", lambda *a, **k: [["git", "tag"]])
    steps, reads, irreversible = pipeline.publish_steps(
        _release(tmp_path), [_Destination([["gh", "x"]], reads=True, calls=calls)])
    assert steps == [["git", "tag"], ["gh", "x"]]
    assert reads is True and irreversible == []


def test_an_irreversible_destination_with_steps_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "tag_steps", lambda *a, **k: [])
    registry = _Destination([["cargo", "publish"]], calls=[])
    registry.reversible = False
    registry.name = "crates-io"
    _, _, irreversible = pipeline.publish_steps(_release(tmp_path), [registry])
    assert irreversible == ["crates-io"]
