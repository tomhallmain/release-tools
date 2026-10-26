"""Stand-ins shared by the tests. Pytest puts this folder on sys.path (it has
no __init__.py), so tests import it as ``helpers``."""
import json
import os
import subprocess

from app_release import record as records
from app_release.model import EXECUTABLE, Artifact

HEAD = "a" * 40
BUILT = "b" * 40
TREE = "t" * 40
REMOTE_HEAD = "r" * 40


def fake_git(head=HEAD, tree=TREE, ancestors=(), tag=None, answers=None):
    """A stand-in for vcs.git that knows HEAD, its tree, which commits are
    ancestors of HEAD, what an existing tag points at, and any other *answers*
    ({args tuple: output})."""
    def git(repo, *args, timeout=None):
        if answers and args in answers:
            return answers[args]
        if args == ("rev-parse", "HEAD"):
            return head
        if args == ("rev-parse", "HEAD^{tree}"):
            return tree
        if args[:2] == ("merge-base", "--is-ancestor"):
            return "" if args[2] in ancestors else None
        if args[:3] == ("rev-parse", "-q", "--verify"):
            return tag
        return None
    return git


def checkout_git(head=HEAD, tree=TREE, dirty=False, ancestors=(), answers=None):
    """fake_git for a checkout whose state rebuild_stale reads."""
    return fake_git(head=head, tree=tree, ancestors=ancestors, answers={
        ("log", "-1", "--format=%ct"): "0",
        ("status", "--porcelain", "--untracked-files=no"): " M file" if dirty else "",
        **(answers or {}),
    })


UPSTREAM = ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")


def upstream_answers(behind=0, fetched=True):
    """checkout_git answers for a branch tracking origin/main, *behind* commits behind it."""
    answers = {UPSTREAM: "origin/main", ("rev-list", "--count", "HEAD..@{upstream}"): str(behind)}
    if fetched:
        answers[("fetch", "--quiet")] = ""
    return answers


def write_record(folder, commit=BUILT, tree=TREE, changes="", version="1.0.0", **extra):
    """A Nuitka-layout build_info.json in *folder*; returns its path."""
    data = {"version": version, "source": {"commit": commit, "changes": changes}, **extra}
    if tree:
        data["source"]["tree"] = tree
    path = os.path.join(str(folder), "build_info.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return path


def built(folder, name="App.exe", product="App", platform="windows-x64", **record_args):
    """A build record and an executable written after it: (record, artifact)."""
    path = write_record(folder, **record_args)
    exe = os.path.join(str(folder), name)
    with open(exe, "wb") as f:
        f.write(b"exe")
    built_at = os.path.getmtime(path)
    os.utime(exe, (built_at + 10, built_at + 10))
    return records.load(path), Artifact(product=product, path=exe, kind=EXECUTABLE, platform=platform)


def recording_run(monkeypatch, module, returncode=0, stdout=""):
    """Replace subprocess.run (as *module* sees it); returns the list of
    (args, kwargs) it was called with."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return calls


class Options:
    """An argparse namespace stand-in for a builder's options."""

    def __init__(self, **values):
        self.toolchain = None
        self.target = None
        self.__dict__.update(values)
