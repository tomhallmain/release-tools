"""Any build system, described in configuration:

    builder = "command"

    [command]
    build = [["make", "dist"]]                     # one or more commands, run in order
    artifacts = [{ glob = "dist/*.zip", kind = "archive" }]
    toolchain_versions = [["gcc", "--version"]]   # recorded with the build
    lock_files = ["Cargo.lock"]

An artifact entry may also give ``product`` (default: the file name up to its
first dot) and ``platform`` ("host", the default for executables, archives and
directories; "any" for none; or a name such as "linux-x64"). A rebuild runs
every command, since which command builds which artifact is not known, and
the tool writes the build record.
"""
import glob
import os
import sys

from app_release import platforms
from app_release.builders.base import Builder, TOOL, run_version
from app_release.model import DIRECTORY, KINDS, PACKAGE, Artifact, BuildRun


class CommandBuilder(Builder):
    name = "command"
    record_owner = TOOL

    def _commands(self) -> list:
        build = self.config.get("build") or []
        if build and all(isinstance(part, str) for part in build):
            build = [build]
        if not build or not all(isinstance(c, list) and c and all(isinstance(p, str) for p in c)
                                for c in build):
            sys.exit('Configuration: [command] build must be a command (["make", "dist"]) '
                     "or a list of them.")
        return build

    def detect(self) -> bool:
        return bool(self.config.get("build"))

    def describe(self) -> str:
        return "[command] table with build commands in its configuration"

    def artifacts(self) -> list:
        entries = self.config.get("artifacts") or []
        if not entries:
            sys.exit("Configuration: [command] artifacts names no files; add e.g. "
                     '{ glob = "dist/*.zip", kind = "archive" }.')
        found = []
        for entry in entries:
            if isinstance(entry, str):
                entry = {"glob": entry}
            kind = entry.get("kind", "archive")
            if kind not in KINDS:
                sys.exit(f"Configuration: artifact kind {kind!r} is not one of {', '.join(KINDS)}.")
            default_platform = "any" if kind == PACKAGE else "host"
            platform = entry.get("platform", default_platform)
            platform = platforms.host() if platform == "host" else None if platform == "any" else platform
            for path in sorted(glob.glob(os.path.join(self.repo, entry["glob"]))):
                if not (os.path.isdir(path) if kind == DIRECTORY else os.path.isfile(path)):
                    continue
                product = entry.get("product") or os.path.basename(path).split(".")[0]
                found.append(Artifact(product=product, path=path, kind=kind, platform=platform))
        return found

    def lock_files(self) -> list:
        return [(os.path.join(self.repo, path), os.path.basename(path))
                for path in self.config.get("lock_files") or []
                if os.path.isfile(os.path.join(self.repo, path))]

    def plan(self, stale: list, record) -> list:
        if getattr(self.options, "toolchain", None):
            sys.exit("--toolchain has no meaning for builder = \"command\"; put it in the build command.")
        return [BuildRun(command=list(command), cwd=self.repo, platform=platforms.host())
                for command in self._commands()]

    def toolchain_versions(self) -> dict:
        versions = {}
        for command in self.config.get("toolchain_versions") or []:
            line = run_version(command)
            if line:
                versions[os.path.basename(command[0])] = line
        return versions
