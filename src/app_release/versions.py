"""Where an app keeps its version, and bumping it.

A source reads one value and writes only that value back, leaving the rest of
the file byte-identical. Configuration names a source as:

    version = "VERSION"                                     # a file holding only the version
    version = { file = "VERSION" }
    version = { pattern = { file = "src/app/version.py", regex = 'APP_VERSION = "([^"]+)"' } }
    version = { toml = { file = "Cargo.toml", key = "package.version" } }
    version = { json = { file = "package.json", key = "version" } }
"""
import json
import os
import re
import sys
import tomllib
from abc import ABC, abstractmethod

from app_release import vcs
from app_release.log import logger

VERSION_PATTERN = r"\d+(\.\d+){0,3}"
BUMP_PARTS = ("major", "minor", "patch")


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


class VersionSource(ABC):
    def __init__(self, repo: str, file: str):
        self.repo = repo
        self.file = file

    def path(self) -> str:
        return os.path.join(self.repo, self.file)

    @abstractmethod
    def read(self) -> str: ...

    @abstractmethod
    def write(self, version: str) -> None: ...

    def _missing(self) -> None:
        sys.exit(f"No version found: {self.path()} does not exist.")


class FileVersion(VersionSource):
    """A file holding only the version, e.g. VERSION."""

    def read(self) -> str:
        if not os.path.isfile(self.path()):
            self._missing()
        return _read_text(self.path()).strip()

    def write(self, version: str) -> None:
        text = _read_text(self.path())
        _write_text(self.path(), version + text[len(text.rstrip()):])


class PatternVersion(VersionSource):
    """The first match of *regex*'s one group in a text file."""

    def __init__(self, repo: str, file: str, regex: str):
        super().__init__(repo, file)
        self.regex = re.compile(regex, re.M)
        if self.regex.groups != 1:
            sys.exit(f"Version pattern {regex!r} must have exactly one group, the version.")

    def _match(self, text: str):
        match = self.regex.search(text)
        if not match:
            sys.exit(f"No version found in {self.path()} by {self.regex.pattern!r}.")
        return match

    def read(self) -> str:
        if not os.path.isfile(self.path()):
            self._missing()
        return self._match(_read_text(self.path())).group(1)

    def write(self, version: str) -> None:
        text = _read_text(self.path())
        match = self._match(text)
        _write_text(self.path(), text[:match.start(1)] + version + text[match.end(1):])


def _toml_line_pattern(table: tuple, leaf: str, text: str):
    """The span of the string value of *leaf* inside table *table* (a tuple of
    names; () for the top level), found line by line; None when absent."""
    current = ()
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        header = re.fullmatch(r"\[\s*([^\[\]]+?)\s*\](\s*#.*)?", stripped)
        if header:
            current = tuple(part.strip().strip("\"'") for part in header.group(1).split("."))
        elif stripped.startswith("[["):
            current = None   # an array of tables holds no version for us
        elif current == table:
            match = re.match(rf"\s*{re.escape(leaf)}\s*=\s*([\"'])([^\"']*)\1", line)
            if match:
                return offset + match.start(2), offset + match.end(2)
        offset += len(line)
    return None


class TomlVersion(VersionSource):
    """A string at a dotted key, e.g. Cargo.toml's ``package.version``, or
    ``workspace.package.version`` when the package says ``version.workspace = true``."""

    def __init__(self, repo: str, file: str, key: str):
        super().__init__(repo, file)
        self.key = key

    def _resolved_key(self, data: dict) -> str:
        value = data
        for part in self.key.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, dict) and value.get("workspace") is True:
            return "workspace." + self.key
        return self.key

    def _value(self, data: dict, key: str):
        value = data
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                sys.exit(f"No version found: {self.path()} has no {key}.")
            value = value[part]
        if not isinstance(value, str):
            sys.exit(f"{self.path()}: {key} is not a string.")
        return value

    def read(self) -> str:
        if not os.path.isfile(self.path()):
            self._missing()
        with open(self.path(), "rb") as f:
            data = tomllib.load(f)
        return self._value(data, self._resolved_key(data))

    def write(self, version: str) -> None:
        text = _read_text(self.path())
        key = self._resolved_key(tomllib.loads(text))
        *table, leaf = key.split(".")
        span = _toml_line_pattern(tuple(table), leaf, text)
        if span is None:
            sys.exit(f"Cannot find the line of {key} in {self.path()} to change; set the version yourself.")
        _write_text(self.path(), text[:span[0]] + version + text[span[1]:])


class JsonVersion(VersionSource):
    """A top-level string in a JSON file, e.g. package.json's ``version``."""

    def __init__(self, repo: str, file: str, key: str = "version"):
        super().__init__(repo, file)
        if "." in key:
            sys.exit(f"JSON version key {key!r}: only a top-level key is supported.")
        self.key = key

    def read(self) -> str:
        if not os.path.isfile(self.path()):
            self._missing()
        value = json.loads(_read_text(self.path())).get(self.key)
        if not isinstance(value, str):
            sys.exit(f"No version found: {self.path()} has no string {self.key!r}.")
        return value

    def write(self, version: str) -> None:
        PatternVersion(self.repo, self.file, rf'"{re.escape(self.key)}"\s*:\s*"([^"]*)"').write(version)


def from_config(repo: str, spec):
    """The source *spec* (from configuration) names; None when *spec* is None."""
    if spec is None:
        return None
    if isinstance(spec, str):
        return FileVersion(repo, spec)
    if isinstance(spec, dict) and len(spec) == 1:
        kind, value = next(iter(spec.items()))
        if kind == "file" and isinstance(value, str):
            return FileVersion(repo, value)
        if kind == "pattern" and isinstance(value, dict):
            return PatternVersion(repo, value["file"], value["regex"])
        if kind == "toml" and isinstance(value, dict):
            return TomlVersion(repo, value["file"], value["key"])
        if kind == "json" and isinstance(value, dict):
            return JsonVersion(repo, value["file"], value.get("key", "version"))
    sys.exit(f"Configuration: cannot read version = {spec!r}; give a file, or one of "
             "pattern, toml or json (see the README).")


def bumped(version: str, part: str) -> str:
    """*version* with *part* raised by one and the parts after it zeroed."""
    numbers = [int(n) for n in version.split(".")]
    numbers += [0] * (3 - len(numbers))
    index = BUMP_PARTS.index(part)
    numbers[index] += 1
    numbers[index + 1:] = [0] * (len(numbers) - index - 1)
    return ".".join(map(str, numbers))


def bump(repo: str, source: VersionSource, version: str, part: str, no_build: bool) -> str:
    """Commit *version* bumped by *part* to *source* and return the new version,
    or *version* while tag v<version> does not exist."""
    if not re.fullmatch(VERSION_PATTERN, version):
        sys.exit(f"Version {version!r} is not up to four dot-separated numbers; cannot bump it.")
    if not vcs.git(repo, "rev-parse", "HEAD"):
        sys.exit("git cannot tell which commit the checkout is at; cannot bump the version.")
    if not vcs.git(repo, "rev-parse", "-q", "--verify", f"refs/tags/v{version}"):
        logger.info(f"Tag v{version} does not exist yet; keeping version {version}.")
        return version
    new = bumped(version, part)
    if no_build:
        sys.exit(f"--bump to {new} needs a rebuild, since the build embeds the version; "
                 "leave out --no-build.")
    if vcs.dirty(repo) is not False:
        sys.exit("Tracked files have uncommitted changes; commit or stash them before --bump.")
    if vcs.git(repo, "rev-parse", "-q", "--verify", f"refs/tags/v{new}"):
        sys.exit(f"Tag v{new} exists already; set the version yourself.")
    source.write(new)
    if vcs.git(repo, "commit", "-m", f"Version {new}", "--", source.path()) is None:
        source.write(version)
        sys.exit(f"git commit of {source.path()} failed; the version stays {version}.")
    logger.info(f"Version {version} -> {new}, committed as {vcs.git(repo, 'rev-parse', '--short=10', 'HEAD')}.")
    return new
