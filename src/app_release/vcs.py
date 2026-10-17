"""git: the checkout's state, tags, and the steps that tag and push a release.

Every other module calls ``vcs.git`` through this module, so tests replace
that one function.
"""
import hashlib
import subprocess
import sys

from app_release.log import logger


def git(repo: str, *args: str):
    """git's stripped stdout, or None when git is missing or the command fails."""
    try:
        result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def in_history(repo: str, commit: str, head: str) -> bool:
    """Whether *commit* is *head* or one of its ancestors."""
    return git(repo, "merge-base", "--is-ancestor", commit, head) is not None


def tagged_commit(repo: str, tag: str):
    """The commit tag *tag* points at here, or None."""
    return git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}")


def dirty(repo: str):
    """Whether tracked files have uncommitted changes; None when git cannot tell."""
    status = git(repo, "status", "--porcelain", "--untracked-files=no")
    return None if status is None else bool(status)


def changes_fingerprint(repo: str):
    """"" for a clean checkout, a SHA-1 of ``git diff HEAD`` otherwise, None when
    git cannot tell. Untracked files are not covered."""
    state = dirty(repo)
    if state is None:
        return None
    if not state:
        return ""
    diff = git(repo, "diff", "HEAD")
    return hashlib.sha1(diff.encode("utf-8")).hexdigest() if diff is not None else None


def tag_steps(repo: str, tag: str, commit: str, remote: str, retag: bool = False) -> list:
    """The git commands that tag *commit* and push it and its branch, leaving
    out what is done already, so that a rerun resumes."""
    on_remote = git(repo, "ls-remote", "--tags", remote, f"refs/tags/{tag}")
    if on_remote is None:
        sys.exit(f"Cannot reach the git remote {remote}.")
    steps = []
    local = git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}")
    if not local:
        steps.append(["git", "tag", tag, commit])
    elif retag and local != commit:
        steps.append(["git", "tag", "-f", tag, commit])
    # A pushed tag carries its commits, but the branch on the remote stays where it was.
    branch = git(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if not branch:
        logger.warning(f"HEAD is not on a branch; push the branch with {commit[:10]} to {remote} yourself.")
    else:
        remote_head = (git(repo, "ls-remote", remote, f"refs/heads/{branch}") or "").split("\t")[0]
        # False also when the remote has commits not fetched here; the push then fails and says so.
        if not remote_head or not in_history(repo, commit, remote_head):
            steps.append(["git", "push", remote, f"refs/heads/{branch}"])
    if not on_remote:
        steps.append(["git", "push", remote, f"refs/tags/{tag}"])
    elif retag and on_remote.split("\t")[0] != commit:
        steps.append(["git", "push", "--force", remote, f"refs/tags/{tag}"])
    return steps
