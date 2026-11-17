"""Keeps every test away from the developer's own state.

Outside the checkout it is given, app-release reaches its run log folder,
git (which searches the folders above the one it is given for a repository,
and reads the user's git config), gh (the user's login), cargo's target
folder, the memory-wait plugin's variables and $EDITOR. Each test starts with
all of these pointed into its tmp_path or cleared; a test that needs one set
sets it itself. Stubbing vcs.git and gh stays each test's job: this is the
net under a test that forgets.
"""
import os
import sys

import pytest

# Each would point git at another repository, work tree or index than the one
# a command names.
GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE")
GH_LOGINS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
             "GH_HOST", "GH_REPO")
CLEARED = ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET", "CARGO_BUILD_TARGET_DIR",
           "NUITKA_MEMORY_WAIT_PLUGIN", "BUILD_MIN_FREE_MEMORY_GB")


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("APP_RELEASE_LOG_DIR", str(tmp_path / "app-release-logs"))

    # git stops searching at pytest's base temp folder, so a test folder is
    # never taken for part of a repository above it, and reads no user or
    # system config (signing, hooks, credential helpers).
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path_factory.getbasetemp()))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for name in GIT_REDIRECTS:
        monkeypatch.delenv(name, raising=False)

    # gh with an empty config folder and no token is logged out, so a real gh
    # call can read or change nothing on GitHub.
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh-config"))
    monkeypatch.setenv("GH_PROMPT_DISABLED", "1")
    for name in GH_LOGINS:
        monkeypatch.delenv(name, raising=False)

    for name in CLEARED:
        monkeypatch.delenv(name, raising=False)
    # An editor that exits at once, for a notes edit a test did not stub.
    monkeypatch.setenv("EDITOR", f'"{sys.executable}" -c pass')
