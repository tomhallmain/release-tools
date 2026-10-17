"""Build records: reading the JSON layout into a BuildRecord, and the record the
tool writes for build systems that keep none.

The layout, schema 1 (an app writes ``schema`` only from 2 on):

    {"version": "1.0.0", "build_id": "...",
     "source": {"commit": "...", "tree": "...", "changes": "", "repo": "..."},
     "python": "3.11.9", "packages": {...}, "with_oqs": true,          # as Nuitka apps write it
     "toolchain": {...}, "dependencies": {...}, "features": {...},    # as this tool writes it
     "options": [...]}
"""
import json
import os
import shutil
import sys
import time

from app_release import __version__, vcs
from app_release.log import logger
from app_release.model import BuildRecord

SCHEMA = 1


def load(path: str):
    """The BuildRecord in the JSON file at *path*, or None when there is none."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    schema = data.get("schema", 1) if isinstance(data, dict) else None
    if not isinstance(schema, int) or schema > SCHEMA:
        sys.exit(f"{path} has build record schema {schema!r}; app-release {__version__} reads "
                 f"up to {SCHEMA}. Update app-release.")
    return from_dict(path, data, os.path.getmtime(path))


def from_dict(path: str, data: dict, written_at: float) -> BuildRecord:
    source = data.get("source") or {}
    toolchain = dict(data.get("toolchain") or {})
    if data.get("python") and "Python" not in toolchain:
        toolchain["Python"] = data["python"]
    features = dict(data.get("features") or {})
    if "with_oqs" in data:
        features.setdefault("oqs", data["with_oqs"])
    return BuildRecord(
        path=path,
        written_at=written_at,
        version=data.get("version"),
        commit=source.get("commit") or None,
        tree=source.get("tree") or None,
        changes=source.get("changes"),
        toolchain=toolchain,
        dependencies=dict(data.get("dependencies") or data.get("packages") or {}),
        features=features,
        options=list(data.get("options") or []),
        embedded_paths=[source["repo"]] if source.get("repo") else [],
        build_id=data.get("build_id"),
    )


def restore(source: str, record_path: str, artifacts) -> None:
    """Copy *source* to *record_path*, dated before the oldest artifact, so the
    checks take it as the record those artifacts were built with."""
    try:
        with open(source, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"--build-info: cannot read {source}: {e}")
    commit = data.get("source", {}).get("commit") if isinstance(data, dict) else None
    if not commit or not data.get("version"):
        sys.exit(f"--build-info: {source} names no version and commit; is it a build record?")
    if os.path.abspath(source) != os.path.abspath(record_path):
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        shutil.copyfile(source, record_path)
    oldest = min(os.path.getmtime(a.path) for a in artifacts)
    os.utime(record_path, (oldest - 1, oldest - 1))
    logger.info(f"Restored {record_path} from {source}: version {data['version']}, "
                f"build {data.get('build_id', '?')}, commit {commit[:10]}.")


def source_state(repo: str) -> dict:
    """The checkout's commit, tree and uncommitted-changes fingerprint, taken
    before a build so the record describes what was built."""
    return {"commit": vcs.git(repo, "rev-parse", "HEAD"),
            "tree": vcs.git(repo, "rev-parse", "HEAD^{tree}"),
            "changes": vcs.changes_fingerprint(repo)}


def write(path: str, version: str, source: dict, started: float, toolchain: dict,
          dependencies: dict, features: dict, options: list) -> None:
    """Write the tool's record of a build that succeeded, dated *started* (the
    build's start), so every artifact the build wrote is newer than it."""
    data = {
        "schema": SCHEMA,
        "version": version,
        "build_id": time.strftime("%Y%m%d-%H%M%S", time.localtime(started)),
        "source": source,
        "toolchain": toolchain,
        "dependencies": dependencies,
        "features": features,
        "options": options,
        "written_by": f"app-release {__version__}",
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(temporary, path)
    # A second early: file times can come from a coarser clock than time.time().
    os.utime(path, (started - 1, started - 1))
    logger.info(f"Wrote the build record {path}.")
