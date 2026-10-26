"""app_release.vcs: the tag and push steps, with git stubbed."""

import pytest

from app_release import vcs
from helpers import HEAD, REMOTE_HEAD


def _remote_git(remote_has_commit, branch="master", reachable=True, local_tag=None, remote_tag=""):
    def git(repo, *args):
        if args[0] == "ls-remote" and args[2].startswith("refs/tags/"):
            return remote_tag if reachable else None
        if args[:3] == ("rev-parse", "-q", "--verify"):
            return local_tag
        if args[:2] == ("symbolic-ref", "--short"):
            return branch
        if args[0] == "ls-remote":
            return f"{REMOTE_HEAD}\trefs/heads/{branch}"
        if args[:2] == ("merge-base", "--is-ancestor"):
            return "" if remote_has_commit and args[3] == REMOTE_HEAD else None
        return None
    return git


def test_branch_is_pushed_before_the_tag_when_the_remote_lacks_the_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=False))
    assert vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin") == [
        ["git", "tag", "v1.0.0", HEAD],
        ["git", "push", "origin", "refs/heads/master"],
        ["git", "push", "origin", "refs/tags/v1.0.0"]]


def test_branch_is_not_pushed_when_the_remote_has_the_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True))
    steps = vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin")
    assert ["git", "push", "origin", "refs/heads/master"] not in steps
    assert ["git", "push", "origin", "refs/tags/v1.0.0"] in steps


def test_an_unreachable_remote_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True, reachable=False))
    with pytest.raises(SystemExit):
        vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin")


def test_retag_moves_the_tag_here_and_there(tmp_path, monkeypatch):
    old = "c" * 40
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True, local_tag=old,
                                                remote_tag=f"{old}\trefs/tags/v1.0.0"))
    steps = vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin", retag=True)
    assert ["git", "tag", "-f", "v1.0.0", HEAD] in steps
    assert ["git", "push", "--force", "origin", "refs/tags/v1.0.0"] in steps


def test_done_steps_are_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True, local_tag=HEAD,
                                                remote_tag=f"{HEAD}\trefs/tags/v1.0.0"))
    assert vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin") == []


def test_the_remote_tag_resolves_an_annotated_tag_to_its_commit(tmp_path, monkeypatch):
    tag_object = "o" * 40
    listed = f"{tag_object}\trefs/tags/v1.0.0\n{HEAD}\trefs/tags/v1.0.0^{{}}"
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True, remote_tag=listed))
    assert vcs.remote_tagged_commit(str(tmp_path), "v1.0.0", "origin") == HEAD


def test_the_remote_tag_is_empty_when_absent_and_none_when_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True))
    assert vcs.remote_tagged_commit(str(tmp_path), "v1.0.0", "origin") == ""
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True, reachable=False))
    assert vcs.remote_tagged_commit(str(tmp_path), "v1.0.0", "origin") is None


def test_a_remote_tag_on_the_build_is_copied_here_without_a_push(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True))
    assert vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin", on_remote=HEAD) == [
        ["git", "tag", "v1.0.0", HEAD]]


def test_a_stale_local_tag_is_moved_to_match_the_remote(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True, local_tag="c" * 40))
    assert vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin", on_remote=HEAD) == [
        ["git", "tag", "-f", "v1.0.0", HEAD]]


def test_retag_does_not_force_push_an_annotated_tag_already_on_the_build(tmp_path, monkeypatch):
    listed = f"{'o' * 40}\trefs/tags/v1.0.0\n{HEAD}\trefs/tags/v1.0.0^{{}}"
    monkeypatch.setattr(vcs, "git", _remote_git(remote_has_commit=True, local_tag=HEAD, remote_tag=listed))
    assert vcs.tag_steps(str(tmp_path), "v1.0.0", HEAD, "origin", retag=True) == []


def test_commits_behind_upstream_fetches_with_the_long_timeout(tmp_path, monkeypatch):
    timeouts = {}

    def git(repo, *args, timeout=30):
        timeouts[args[0]] = timeout
        return {"fetch": "", "rev-list": "3"}.get(args[0])

    monkeypatch.setattr(vcs, "git", git)
    assert vcs.commits_behind_upstream(str(tmp_path)) == 3
    assert timeouts["fetch"] == vcs.FETCH_TIMEOUT


def test_commits_behind_upstream_is_none_when_the_fetch_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "git", lambda repo, *args, timeout=30: "0" if args[0] == "rev-list" else None)
    assert vcs.commits_behind_upstream(str(tmp_path)) is None


def test_changes_fingerprint(tmp_path, monkeypatch):
    answers = {("status", "--porcelain", "--untracked-files=no"): "", ("diff", "HEAD"): "diff"}
    monkeypatch.setattr(vcs, "git", lambda repo, *args: answers.get(args))
    assert vcs.changes_fingerprint(str(tmp_path)) == ""
    answers[("status", "--porcelain", "--untracked-files=no")] = " M a.py"
    assert len(vcs.changes_fingerprint(str(tmp_path))) == 40
    monkeypatch.setattr(vcs, "git", lambda repo, *args: None)
    assert vcs.changes_fingerprint(str(tmp_path)) is None
