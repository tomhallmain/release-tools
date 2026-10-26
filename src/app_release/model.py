"""The data the pipeline passes between the builder, the checks, staging and
the destinations."""
import os
from dataclasses import dataclass, field
from typing import Optional

# Artifact kinds. An executable or archive belongs to a platform; a package
# (.whl, .gem, .crate) may not. A directory is zipped when staged.
EXECUTABLE = "executable"
ARCHIVE = "archive"
PACKAGE = "package"
DIRECTORY = "dir"
KINDS = (EXECUTABLE, ARCHIVE, PACKAGE, DIRECTORY)


@dataclass
class Artifact:
    product: str             # "Weidr", "WeidrHeadless", "mytool"
    path: str
    kind: str = EXECUTABLE
    platform: Optional[str] = None   # "windows-x64"; None when platform-independent

    @property
    def extension(self) -> str:
        """The extension of the staged file: ".zip" for a directory."""
        if self.kind == DIRECTORY:
            return ".zip"
        name = os.path.basename(self.path)
        for double in (".tar.gz", ".tar.xz", ".tar.bz2"):
            if name.endswith(double):
                return double
        return os.path.splitext(name)[1]

    @property
    def key(self) -> str:
        """Names the artifact in messages and among several platforms of one product."""
        return f"{self.product} ({self.platform})" if self.platform else self.product


@dataclass
class BuildRecord:
    """A build's account of itself, read from whichever file holds it."""
    path: str
    written_at: float        # an artifact older than this predates the build
    version: Optional[str] = None
    commit: Optional[str] = None
    tree: Optional[str] = None
    changes: Optional[str] = None    # "" clean, None unknown
    toolchain: dict = field(default_factory=dict)      # {"Python": "3.11.9"}, {"rustc": "1.82.0"}
    dependencies: dict = field(default_factory=dict)   # name -> version, for the notes
    features: dict = field(default_factory=dict)       # {"oqs": True}
    options: list = field(default_factory=list)        # build options, when the tool ran the build
    embedded_paths: list = field(default_factory=list) # text a public binary carries, e.g. the checkout path
    build_id: Optional[str] = None


@dataclass
class BuildRun:
    """One build command."""
    command: list
    cwd: str
    env: Optional[dict] = None
    platform: Optional[str] = None


@dataclass
class Asset:
    """A staged file, under its release name."""
    name: str
    path: str
    artifact: Optional[Artifact] = None   # None for lock files, checksums


@dataclass
class ReleasePlan:
    repo: str
    tag: str
    commit: str
    remote: str
    title: str
    assets: list             # Asset
    notes: str               # path, relative to the repo
    publish: bool = False
    retag: bool = False
    remote_tag: Optional[str] = None   # the tag's commit on the remote, "" when absent; None when not looked up
    platforms: list = field(default_factory=list)   # what publishing requires, from the configuration
    force: bool = False
    merge_notes: bool = True   # False with --notes FILE: an existing release's notes are left alone

    @property
    def version(self) -> str:
        return self.tag[1:]

    def files(self, assets=None) -> list:
        """Repo-relative, slash-separated paths of *assets* (default: all)."""
        return [os.path.relpath(a.path, self.repo).replace(os.sep, "/")
                for a in (self.assets if assets is None else assets)]
