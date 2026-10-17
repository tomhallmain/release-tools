"""The build system seam: where artifacts are, how to rebuild them, and where
their build record is."""
import os
import subprocess
import sys
import time
from abc import ABC, abstractmethod

from app_release import record as records
from app_release.log import command_line, timed

APP = "app"     # the build writes its own record
TOOL = "tool"   # this tool writes the record when it runs the build
TOOL_DIR = ".release"


class Builder(ABC):
    name = ""
    record_owner = TOOL
    # Configuration defaults this build system brings; the app's own entries win.
    default_features: dict = {}
    default_variant_hints: dict = {}

    def __init__(self, repo: str, config: dict, options):
        self.repo = repo
        # This builder's own table, e.g. [cargo]; the whole configuration is in self.app_config.
        self.config = config.get(self.name) or {}
        self.app_config = config
        self.options = options

    # Options and actions available before a checkout is known.

    @classmethod
    def add_options(cls, parser) -> None:
        """Add this build system's command line options to *parser*."""

    @classmethod
    def early_action(cls, opts) -> bool:
        """Do what an option asks that needs no checkout; True when done."""
        return False

    # Locations.

    def work_dir(self) -> str:
        """Where the log, the tool-written record and staging go."""
        return os.path.join(self.repo, TOOL_DIR)

    def log_dir(self) -> str:
        return os.path.join(self.work_dir(), "logs")

    def stage_dir(self, tag: str) -> str:
        return os.path.join(self.work_dir(), "stage", tag)

    def record_path(self) -> str:
        return os.path.join(self.work_dir(), "build_record.json")

    # What the build system is.

    @abstractmethod
    def detect(self) -> bool:
        """Whether the checkout looks like this build system's."""

    def describe(self) -> str:
        """What detect() looks for, for the message when it finds nothing."""
        return f"{self.name} project files"

    @abstractmethod
    def artifacts(self) -> list:
        """The artifacts the last build left, as Artifact."""

    def default_version_source(self):
        """The VersionSource used when the configuration names none."""
        from app_release.versions import FileVersion
        return FileVersion(self.repo, "VERSION")

    def lock_files(self) -> list:
        """(path, label) of each lock file to publish; the label ends its asset name."""
        return []

    def note_lines(self, record) -> list:
        return []

    # The build record.

    def read_record(self):
        return records.load(self.record_path())

    def restore_record(self, source: str, artifacts: list) -> None:
        records.restore(source, self.record_path(), artifacts)

    # Building.

    @abstractmethod
    def plan(self, stale: list, record) -> list:
        """The BuildRuns that rebuild the *stale* artifacts; exits when it cannot."""

    def toolchain_versions(self) -> dict:
        """What built it, for a tool-written record: {"rustc": "1.82.0"}."""
        return {}

    def dependency_versions(self) -> dict:
        return {}

    def build(self, runs: list, extra_args: list, version: str, features: dict) -> None:
        """Run *runs* with *extra_args* after each command; exits on a failure.
        With a tool-written record, writes it once every run succeeded."""
        source = records.source_state(self.repo) if self.record_owner == TOOL else None
        started = time.time()
        for run in runs:
            command = [*run.command, *extra_args]
            with timed(f"Building: {command_line(command)}"):
                if subprocess.run(command, cwd=run.cwd, env=run.env).returncode != 0:
                    sys.exit("The build failed; nothing packaged.")
        if self.record_owner == TOOL:
            records.write(self.record_path(), version, source, started, self.toolchain_versions(),
                          self.dependency_versions(), features, list(extra_args))


def run_version(command: list):
    """The first line *command* prints, or None when it does not run."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = (result.stdout or result.stderr).strip().splitlines()
    return lines[0].strip() if lines else None
