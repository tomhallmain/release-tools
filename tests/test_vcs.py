"""app_release.vcs: the tag and push steps, with git stubbed."""

import pytest

from app_release import vcs
from helpers import HEAD, REMOTE_HEAD


def _remote_git(remote_has_commit, branch="master", reachable=True, local_tag=None, remote_tag=""):
    def git(repo, *args):
        if args[:2] == ("ls-remote", "--tags"):
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


def test_changes_fingerprint(tmp_path, monkeypatch):
    answers = {("status", "--porcelain", "--untracked-files=no"): "", ("diff", "HEAD"): "diff"}
    monkeypatch.setattr(vcs, "git", lambda repo, *args: answers.get(args))
    assert vcs.changes_fingerprint(str(tmp_path)) == ""
    answers[("status", "--porcelain", "--untracked-files=no")] = " M a.py"
    assert len(vcs.changes_fingerprint(str(tmp_path))) == 40
    monkeypatch.setattr(vcs, "git", lambda repo, *args: None)
    assert vcs.changes_fingerprint(str(tmp_path)) is None
