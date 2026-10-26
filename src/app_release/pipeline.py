"""The release steps that know no language: what is behind, the checks,
staging, release notes, and the order of the publishing commands."""
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile

from app_release import __version__, platforms, vcs
from app_release.log import logger
from app_release.model import DIRECTORY, EXECUTABLE, Asset
from app_release.versions import VERSION_PATTERN


# What is behind

def behind(repo, artifact, version, record, head, head_tree, head_time):
    """Why *artifact* is behind commit *head*, or None when it was built from
    it, or from an ancestor with the same files (tree)."""
    if record is None:
        # No build record: only the time is known.
        if os.path.getmtime(artifact.path) < head_time:
            return "it is older than the checkout's last commit"
        return None
    if os.path.getmtime(artifact.path) < record.written_at:
        return "it predates the last build, so its source is unknown"
    built = record.commit or ""
    if built != head:
        if not record.tree or record.tree != head_tree:
            return f"it was built from {built[:10] or 'an unknown commit'}, the checkout is at {head[:10]}"
        if not vcs.in_history(repo, built, head):
            # The release tag goes on the build's commit, so it has to be in the history.
            return (f"it was built from {built[:10]}, which has the checkout's files but is not "
                    "in its history (rewritten since), so it cannot be tagged")
    if record.changes:
        return "it was built with uncommitted changes"
    if record.version != version:
        return f"it is version {record.version}, the checkout says {version}"
    return None


def feature_args(record, features: dict, build_args: list) -> list:
    """*build_args* plus the option of each feature the last build had, so a
    rebuild keeps it (e.g. --with-oqs)."""
    args = list(build_args)
    for name, spec in features.items():
        option = spec.get("rebuild_option") if isinstance(spec, dict) else None
        if option and record and record.features.get(name) and option not in args:
            args.append(option)
    return args


def recorded_features(features: dict, build_args: list) -> dict:
    """{feature: whether its option is in *build_args*}, for a tool-written record."""
    return {name: spec["rebuild_option"] in build_args for name, spec in features.items()
            if isinstance(spec, dict) and spec.get("rebuild_option")}


def rebuild_stale(repo, version, artifacts, builder, record, build_args, features) -> bool:
    """Rebuild the artifacts that are behind HEAD; returns whether it built."""
    head = vcs.git(repo, "rev-parse", "HEAD")
    head_tree = vcs.git(repo, "rev-parse", "HEAD^{tree}")
    head_time = vcs.git(repo, "log", "-1", "--format=%ct")
    dirty = vcs.dirty(repo)
    if not head or not head_tree or not head_time or dirty is None:
        sys.exit("git cannot tell which commit the checkout is at; build yourself and pass --no-build.")
    stale = []
    for artifact in artifacts:
        reason = behind(repo, artifact, version, record, head, head_tree, int(head_time))
        if reason:
            logger.info(f"{artifact.key} is behind: {reason}.")
            stale.append(artifact)
    if not stale:
        logger.info(f"Every artifact was built from {head[:10]} or a commit with its files; nothing to rebuild.")
        return False
    if dirty:
        sys.exit("Tracked files have uncommitted changes; commit or stash them, so the build "
                 "matches a commit, and run this again.")
    builder.preflight()
    runs = builder.plan(stale, record)
    builder.build(runs, build_args, version, recorded_features(features, build_args))
    return True


# Checks

def check(repo, version, tag, record, artifacts, variant, variant_hints, max_asset_bytes,
          retag=False, no_build=False, remote_tag=None, remote="origin"):
    """(errors, warnings) found before packaging. *remote_tag* is the commit
    tag *tag* points at on *remote*: "" when it has no such tag, None when unknown."""
    errors, warnings = [], []
    if not re.fullmatch(VERSION_PATTERN, version):
        errors.append(f"Version {version!r} is not up to four dot-separated numbers.")
    head = vcs.git(repo, "rev-parse", "HEAD")
    built = None
    if record is None:
        warnings.append("No build record: cannot check what the artifacts were built from.")
    else:
        built = record.commit
        if record.version != version:
            errors.append(f"The build is version {record.version}, the checkout says {version}.")
        if record.changes:
            errors.append("The build was made with uncommitted changes; commit them and rebuild.")
        elif record.changes is None:
            warnings.append("The build could not tell whether it had uncommitted changes.")
        if built and head and built != head:
            if not record.tree or record.tree != vcs.git(repo, "rev-parse", "HEAD^{tree}"):
                message = f"The build is from commit {built[:10]}, the checkout is at {head[:10]}."
                # Its tag goes on the build's commit, which a later checkout contains.
                if no_build and vcs.in_history(repo, built, head):
                    warnings.append(message + " Packaging it as it is (--no-build).")
                else:
                    errors.append(message)
            elif not vcs.in_history(repo, built, head):
                errors.append(f"The build is from commit {built[:10]}, which has the checkout's files but "
                              "is not in its history (rewritten since the build), so it cannot be tagged; "
                              "rebuild it.")
        for path in record.embedded_paths:
            warnings.append(f"The build embeds the text of its checkout path, {path} "
                            "(not the folder's contents), where anyone who downloads it can read it.")
        for name, hint in variant_hints.items():
            installed = record.dependencies.get(name) or ""
            if isinstance(hint, dict) and hint.get("contains") and hint["contains"] in installed and not variant:
                warnings.append(f"{name} {installed} is a {hint['variant']} build; "
                                f"consider --variant {hint['variant']}.")
        for artifact in artifacts:
            if os.path.getmtime(artifact.path) < record.written_at:
                errors.append(f"{artifact.path} predates the last build; rebuild it, "
                              "or leave it out with --products.")
    tagged = vcs.tagged_commit(repo, tag)
    expected = built or head
    remote_differs = bool(remote_tag and expected and remote_tag != expected)
    if remote_differs:
        # Another machine may have tagged and uploaded a build of that commit already.
        if retag:
            warnings.append(f"Tag {tag} on {remote} points at {remote_tag[:10]}; "
                            f"--retag moves it to {expected[:10]}.")
        else:
            errors.append(f"Tag {tag} on {remote} points at {remote_tag[:10]}, not the build's commit "
                          f"{expected[:10]}; --retag moves it.")
    if tagged and expected and tagged != expected and not (remote_differs and tagged == remote_tag):
        if remote_tag == expected:
            warnings.append(f"Tag {tag} here points at {tagged[:10]}, but on {remote} at the build's "
                            "commit; the release moves it here to match.")
        elif retag:
            warnings.append(f"Tag {tag} points at {tagged[:10]}; --retag moves it to {expected[:10]}.")
        else:
            errors.append(f"Tag {tag} points at {tagged[:10]}, not the build's commit {expected[:10]}; "
                          "--retag moves it.")
    if max_asset_bytes:
        limit = f"{max_asset_bytes / 1024 ** 3:.0f} GiB"
        for artifact in artifacts:
            if artifact.kind == DIRECTORY:
                if _folder_size(artifact.path) >= max_asset_bytes:
                    warnings.append(f"{artifact.path} holds {limit} or more; its staged archive is "
                                    "checked against the asset limit before anything is uploaded.")
            elif os.path.getsize(artifact.path) >= max_asset_bytes:
                errors.append(f"{artifact.path} is {limit} or larger, "
                              "more than a destination accepts as an asset.")
    return errors, warnings


def _folder_size(path: str) -> int:
    return sum(os.path.getsize(os.path.join(folder, name))
               for folder, _, names in os.walk(path) for name in names
               if not os.path.islink(os.path.join(folder, name)))


def staged_too_large(assets, max_asset_bytes) -> list:
    """Errors for staged *assets* of *max_asset_bytes* or more."""
    if not max_asset_bytes:
        return []
    return [f"{a.name} is {os.path.getsize(a.path) / 1024 ** 3:.2f} GiB staged, more than a destination "
            f"accepts as an asset (under {max_asset_bytes / 1024 ** 3:.0f} GiB)."
            for a in assets if os.path.getsize(a.path) >= max_asset_bytes]


# Staging

def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def suffix(version: str, platform, variant) -> str:
    """``<version>[-<platform>][-<variant>]``, the part of an asset name after the product."""
    return "-".join(p for p in (version, platform, variant) if p)


def _archives_unix(artifact, archive_unix: bool) -> bool:
    return (archive_unix and artifact.kind == EXECUTABLE and bool(artifact.platform)
            and not artifact.platform.startswith("windows"))


def _tar_executable(path: str, target: str) -> None:
    """A .tar.gz of the executable at *path*, mode 0755: a browser download of a
    bare file loses its executable bit on macOS and Linux, an archive keeps it."""
    def executable(info):
        info.mode, info.uid, info.gid, info.uname, info.gname = 0o755, 0, 0, "", ""
        return info

    with tarfile.open(target, "w:gz") as archive:
        archive.add(path, arcname=os.path.basename(path), filter=executable)


def _zip_app_bundle(path: str, target: str) -> None:
    """Zip a macOS .app with ditto, which keeps its symlinks and code signature."""
    if not shutil.which("ditto"):
        sys.exit(f"{path} is a macOS app bundle; stage it on the Mac that built it (needs ditto).")
    if subprocess.run(["ditto", "-c", "-k", "--keepParent", path, target]).returncode != 0:
        sys.exit(f"ditto could not zip {path}.")


def stage(out_dir, app_name, version, variant, artifacts, lock_files, archive_unix=True) -> tuple:
    """Copy (or archive) *artifacts* and *lock_files* into *out_dir* under release
    names and write a checksums file per platform. With *archive_unix*, macOS and
    Linux executables are staged as .tar.gz. Returns (downloads, checksum files),
    both lists of Asset."""
    os.makedirs(out_dir, exist_ok=True)
    groups = {}   # platform -> [Asset]
    names = set()

    def add(asset, platform):
        if asset.name in names:
            sys.exit(f"Two artifacts would both be staged as {asset.name}; give them distinct "
                     "product names.")
        names.add(asset.name)
        groups.setdefault(platform, []).append(asset)

    for artifact in artifacts:
        archived = _archives_unix(artifact, archive_unix)
        extension = ".tar.gz" if archived else artifact.extension
        name = f"{artifact.product}-{suffix(version, artifact.platform, variant)}{extension}"
        target = os.path.join(out_dir, name)
        logger.info(f"Staging {artifact.path} -> {name}")
        if artifact.kind == DIRECTORY:
            if os.path.exists(target):
                os.remove(target)
            if artifact.path.rstrip("/\\").endswith(".app"):
                _zip_app_bundle(artifact.path, target)
            else:
                parent, folder = os.path.split(os.path.abspath(artifact.path))
                shutil.make_archive(target[:-len(".zip")], "zip", root_dir=parent, base_dir=folder)
        elif archived:
            _tar_executable(artifact.path, target)
        else:
            shutil.copy2(artifact.path, target)
        add(Asset(name, target, artifact), artifact.platform)
    # The lock files belong to the build: named for its one platform, or for none.
    built_for = {a.platform for a in artifacts}
    build_platform = next(iter(built_for)) if len(built_for) == 1 else None
    for path, label in lock_files:
        name = f"{app_name}-{suffix(version, build_platform, variant)}-{label}"
        shutil.copy2(path, os.path.join(out_dir, name))
        add(Asset(name, os.path.join(out_dir, name)), build_platform)
    downloads = [asset for group in groups.values() for asset in group]
    sums = []
    for platform, assets in groups.items():
        name = f"SHA256SUMS-{suffix(version, platform, variant)}.txt"
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for asset in assets:
                f.write(f"{sha256(asset.path)}  {asset.name}\n")
        sums.append(Asset(name, path))
    return downloads, sums


def checksum_platforms(names, version: str) -> set:
    """The ``<platform>[-<variant>]`` of each ``SHA256SUMS-<version>-...txt`` in
    *names*: one per platform built, since each build writes exactly one."""
    prefix, end = f"SHA256SUMS-{version}-", ".txt"
    return {n[len(prefix):-len(end)] for n in names if n.startswith(prefix) and n.endswith(end)}


def missing_platforms(names, version: str, required) -> list:
    """The platforms in *required* with no checksums file among *names*; any
    variant of a platform counts."""
    built = checksum_platforms(names, version)
    return [p for p in required if not any(b == p or b.startswith(p + "-") for b in built)]


# Release notes

def changes(repo: str, commit: str) -> list:
    """Subjects of the commits since the previous v* tag, as notes lines."""
    previous = vcs.git(repo, "describe", "--tags", "--abbrev=0", "--match", "v*", f"{commit}^")
    if not previous:
        return ["- First release."]
    log = vcs.git(repo, "log", "--no-merges", "--format=- %s", f"{previous}..{commit}")
    return (log.splitlines() if log else ["- No changes."]) + ["", f"Changes since {previous}."]


# Each machine that builds for a release rewrites only its own marked parts of
# the notes, plus the shared commit line and downloads list; text outside the
# markers (hand edits on GitHub) is kept.
PART = re.compile(r"<!-- app-release:(?P<kind>[a-z]+)(?P<attrs>[^>]*?) -->\n.*?<!-- /app-release:(?P=kind) -->\n?",
                  re.S)
ANY_PLATFORM = "any"


def _block(kind: str, lines, attrs: str = "") -> list:
    return [f"<!-- app-release:{kind}{attrs} -->", *lines, f"<!-- /app-release:{kind} -->"]


def downloads_block(sizes: dict) -> list:
    """The marked downloads list for {asset name: size in bytes}, by name; the
    checksums files are left out."""
    lines = [f"- `{name}` ({size / 1024 ** 2:.0f} MB)" for name, size in sorted(sizes.items())
             if not name.startswith("SHA256SUMS-")]
    return _block("downloads", lines)


def note_parts(text: str) -> dict:
    """{(kind, first attribute): (start, end, attributes)} of the marked parts
    of *text*; a platform part's first attribute is its platform."""
    parts = {}
    for match in PART.finditer(text):
        words = match.group("attrs").split()
        attrs = dict(w.split("=", 1) for w in words[1:] if "=" in w)
        parts[(match.group("kind"), words[0] if words else "")] = (match.start(), match.end(), attrs)
    return parts


def platform_commits(text: str) -> dict:
    """{platform: the commit its notes part says it was built from}."""
    return {name: attrs.get("commit", "") for (kind, name), (_, _, attrs) in note_parts(text).items()
            if kind == "platform"}


def merge_notes(body: str, local: str, downloads: list) -> str:
    """The release's notes *body* with the commit line and platform parts of
    this run's notes *local*, and the *downloads* block, put in place of their
    old versions; a platform part the body lacks goes after the last one."""
    body = body.replace("\r\n", "\n")
    if body and not body.endswith("\n"):
        body += "\n"
    replacements = [(key, local[start:end]) for key, (start, end, _) in note_parts(local).items()
                    if key[0] in ("commit", "platform")]
    replacements.append((("downloads", ""), "\n".join(downloads) + "\n"))
    for key, part in replacements:
        if not part.endswith("\n"):
            part += "\n"
        parts = note_parts(body)
        if key in parts:
            start, end, _ = parts[key]
            body = body[:start] + part + body[end:]
        elif key[0] == "platform":
            ends = [end for (kind, _), (_, end, _) in parts.items() if kind == "platform"]
            if ends:
                body = body[:max(ends)] + "\n" + part + body[max(ends):]
            else:
                body += "\n## Platforms\n\n" + part
        elif key[0] == "downloads":
            body += "\n## Downloads\n\n" + part
    return body


def developer_id_signed(path: str) -> bool:
    """Whether *path* has a Developer ID signature; an ad-hoc one does not count."""
    try:
        result = subprocess.run(["codesign", "-dv", "--verbose=2", path], capture_output=True, text=True,
                                timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "Authority=Developer ID Application" in result.stderr


def _platform_lines(platform, artifacts, notes_config) -> list:
    """Notes lines about running *platform*'s build."""
    runnable = [a for a in artifacts if a.platform == platform and a.kind in (EXECUTABLE, DIRECTORY)]
    if not runnable:
        return []
    if platform.startswith("windows"):
        if notes_config.get("unsigned_windows", True) and any(a.kind == EXECUTABLE for a in runnable):
            return ["- The executable is unsigned: Windows SmartScreen asks for confirmation "
                    "(More info, then Run anyway)."]
    elif platform.startswith("macos"):
        if notes_config.get("unsigned_macos", True) and not all(developer_id_signed(a.path) for a in runnable):
            return ["- The app is not notarized, so macOS blocks it the first time: in System Settings > "
                    "Privacy & Security, choose Open Anyway (up to macOS 14, right-clicking it and "
                    "choosing Open also works)."]
    elif platform.startswith("linux") and platform == platforms.host():
        glibc = platforms.glibc_version()
        if notes_config.get("linux_glibc", True) and glibc:
            return [f"- Needs glibc {glibc} or newer."]
    return []


def release_notes(repo, title, record, commit, artifacts, downloads, builder_lines=(),
                  features=None, notes_config=None) -> str:
    notes_config = notes_config or {}
    built_with = ""
    if record:
        toolchain = ", ".join(f"{n} {v}" for n, v in record.toolchain.items())
        dependencies = ", ".join(f"{n} {v}" for n, v in sorted(record.dependencies.items()))
        built_with = ", ".join(part for part in (toolchain, dependencies) if part)
    feature_lines = []
    for name, spec in (features or {}).items():
        note = spec.get("note") if isinstance(spec, dict) else None
        if note and record and name in record.features:
            feature_lines.append(f"- {note}: {'included' if record.features[name] else 'not included'}.")
    lines = [f"# {title}", "", *_block("commit", [f"Built from commit {commit[:10]}."]),
             "", "## Changes", "", *changes(repo, commit),
             "", "## Downloads", "", *downloads_block({a.name: os.path.getsize(a.path) for a in downloads}),
             "", "## Platforms", ""]
    for platform in sorted({a.platform for a in artifacts if a.platform}) or [ANY_PLATFORM]:
        part = [f"**{platform}**, built from commit {commit[:10]}" + (f" with {built_with}." if built_with else "."),
                *feature_lines, *_platform_lines(platform, artifacts, notes_config)]
        lines += [*_block("platform", part, f" {platform} commit={commit}"), ""]
    lines += ["## Notes", "", f"- Packaged with app-release {__version__}.", *builder_lines]
    lines += list(notes_config.get("extra") or [])
    return "\n".join(lines) + "\n"


def write_generated_notes(path: str, text: str) -> bool:
    """Write the generated notes *text* to *path*, unless the file there was
    edited since this tool last wrote it. Returns whether it wrote."""
    # Holds the hash of the last generated text; a file whose hash differs was edited.
    stamp = path + ".generated"
    if os.path.isfile(path) and os.path.isfile(stamp):
        with open(stamp, "r", encoding="utf-8") as f:
            if f.read().strip() != sha256(path):
                return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(sha256(path) + "\n")
    return True


def edit(path: str) -> None:
    """Open *path* in $EDITOR (Notepad on Windows when unset) and wait for it to close."""
    editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "vi")
    try:
        if sys.platform == "win32":
            # A Windows command line is one string; this keeps any quotes $EDITOR has.
            result = subprocess.run(f'{editor} "{path}"')
        else:
            result = subprocess.run([*shlex.split(editor), path])
    except OSError as e:
        logger.warning(f"Cannot start the editor {editor!r}: {e}")
        return
    if result.returncode != 0:
        logger.warning(f"The editor {editor!r} exited with code {result.returncode}.")


def approve_notes(path: str) -> None:
    """Show the release notes at *path* and ask to approve, edit or abort;
    exits on abort."""
    while True:
        with open(path, "r", encoding="utf-8") as f:
            print(f"\n----- {path} -----\n{f.read().rstrip()}\n-----")
        answer = input("Release notes: [a]pprove, [e]dit, or abort? [a/e/N] ").strip().lower()
        if answer in ("a", "approve"):
            return
        if answer not in ("e", "edit"):
            sys.exit("Not run.")
        print("Waiting for the editor to close (an editor that returns at once, such as "
              "`code`, needs its wait option, e.g. EDITOR=\"code --wait\").")
        edit(path)


# Publishing

def publish_steps(release, destinations) -> tuple:
    """(steps, whether a step reads the notes, names of the destinations with
    steps that cannot be undone). Each destination is checked before anything is
    planned, so a missing login cannot stop the run after the tag is pushed."""
    for destination in destinations:
        destination.check_ready()
    steps = vcs.tag_steps(release.repo, release.tag, release.commit, release.remote, release.retag,
                          release.remote_tag)
    reads_notes = False
    irreversible = []
    for destination in destinations:
        own = destination.plan(release)
        steps += own
        reads_notes = reads_notes or destination.reads_notes(own)
        if destination.irreversible(own):
            irreversible.append(destination.name)
    return steps, reads_notes, irreversible
