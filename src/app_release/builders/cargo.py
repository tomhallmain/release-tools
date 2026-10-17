"""Rust apps built with Cargo.

    builder = "cargo"

    [cargo]
    targets = ["x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"]   # default: the host only
    bins = ["mytool"]                                                 # default: the package's binaries

The version is Cargo.toml's ``package.version`` (or the workspace's), the lock
file Cargo.lock. ``--target`` replaces the configured targets for one run and
``--toolchain`` names a rustup toolchain (``cargo +<toolchain>``). A rebuild
builds every target, so the one build record covers every artifact.
"""
import os
import sys
import tomllib

from app_release import platforms
from app_release.builders.base import Builder, TOOL, run_version
from app_release.model import EXECUTABLE, Artifact, BuildRun
from app_release.versions import TomlVersion

MANIFEST = "Cargo.toml"


class CargoBuilder(Builder):
    name = "cargo"
    record_owner = TOOL

    @classmethod
    def add_options(cls, parser) -> None:
        parser.add_argument("--target", action="append", metavar="TRIPLE",
                            help="(cargo) build for this target instead of the configured ones; repeatable")

    def _manifest(self) -> dict:
        with open(os.path.join(self.repo, MANIFEST), "rb") as f:
            return tomllib.load(f)

    def _targets(self) -> list:
        """Target triples, or [None] for the host."""
        return getattr(self.options, "target", None) or self.config.get("targets") or [None]

    def _bins(self) -> list:
        if self.config.get("bins"):
            return list(self.config["bins"])
        manifest = self._manifest()
        names = [b["name"] for b in manifest.get("bin", []) if b.get("name")]
        package = manifest.get("package", {}).get("name")
        if not names and package:
            names = [package]
        if not names:
            sys.exit(f"{MANIFEST} names no binary; set bins in [cargo].")
        return names

    def _cargo(self) -> list:
        toolchain = getattr(self.options, "toolchain", None)
        return ["cargo", f"+{toolchain}"] if toolchain else ["cargo"]

    def detect(self) -> bool:
        return os.path.isfile(os.path.join(self.repo, MANIFEST))

    def describe(self) -> str:
        return f"{MANIFEST} at its root"

    def default_version_source(self):
        return TomlVersion(self.repo, MANIFEST, "package.version")

    def _target_dir(self) -> str:
        return os.environ.get("CARGO_TARGET_DIR") or os.path.join(self.repo, "target")

    def artifacts(self) -> list:
        found = []
        for target in self._targets():
            folder = os.path.join(self._target_dir(), *([target] if target else []), "release")
            windows = platforms.triple_is_windows(target) if target else platforms.host_is_windows()
            platform = platforms.from_triple(target) if target else platforms.host()
            for name in self._bins():
                path = os.path.join(folder, name + (".exe" if windows else ""))
                if os.path.isfile(path):
                    found.append(Artifact(product=name, path=path, kind=EXECUTABLE, platform=platform))
        return found

    def lock_files(self) -> list:
        lock = os.path.join(self.repo, "Cargo.lock")
        return [(lock, "Cargo.lock")] if os.path.isfile(lock) else []

    def plan(self, stale: list, record) -> list:
        runs = []
        for target in self._targets():
            command = [*self._cargo(), "build", "--release"]
            for name in self._bins():
                command += ["--bin", name]
            if target:
                command += ["--target", target]
            runs.append(BuildRun(command=command, cwd=self.repo,
                                 platform=platforms.from_triple(target) if target else platforms.host()))
        return runs

    def toolchain_versions(self) -> dict:
        versions = {}
        for tool in ("rustc", "cargo"):
            command = [tool, *self._cargo()[1:], "--version"]
            line = run_version(command)
            if line:
                versions[tool] = line.split(" ", 1)[1] if line.startswith(tool + " ") else line
        return versions
