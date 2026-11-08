"""GitHub releases through the GitHub CLI (gh, logged in): a draft release
with every staged asset, published with --publish.

    [github]
    keep = 5                     # on publish, keep the downloads of the newest 5 releases
    prune_spare = ["SHA256SUMS-*", "*-requirements.lock.txt"]   # assets pruning never deletes

Pruning deletes assets of older published ``v<version>`` releases; their notes
and tags stay. It skips drafts, pre-releases, the latest release, the one being
published, and immutable releases (GitHub refuses those deletions). Deleting
cannot be undone, so it needs ``--yes-irreversible``.

Several machines can add to one release. A run that finds the release uploads
its assets and merges its notes, replacing the marked parts it owns (commit
line, downloads list, its platform's part); notes without marked parts, and
runs with ``--notes``, leave them alone. ``--publish`` refuses while a
configured platform has no ``SHA256SUMS-<version>-<platform>`` asset or a
platform part names a commit other than the tag's; ``--retag`` refuses while
other platforms' builds are in the release. ``--force`` overrides each.
"""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys

from app_release import pipeline
from app_release.destinations.base import Destination
from app_release.lib.translations import _
from app_release.log import command_line, logger
from app_release.versions import VERSION_PATTERN

DEFAULT_SPARE = ["SHA256SUMS-*", "*-requirements.lock.txt"]
# gh release list returns 30 releases unless told otherwise.
LIST_LIMIT = "1000"


def _version_key(tag: str) -> tuple:
    return tuple(int(n) for n in tag[1:].split("."))


class GitHubRelease(Destination):
    name = "github"
    # GitHub refuses release assets of 2 GiB or more.
    max_asset_bytes = 2 * 1024 ** 3

    def check_ready(self) -> None:
        if not shutil.which("gh"):
            sys.exit(_("gh (GitHub CLI) is not installed; install it, run `gh auth login`, "
                       "or pass --no-upload."))
        # Checked before anything runs, so a missing login cannot stop the run after the tag is pushed.
        if subprocess.run(["gh", "auth", "status"], cwd=self.repo, capture_output=True).returncode != 0:
            sys.exit(_("gh is not logged in to GitHub; run `gh auth login` (or set GH_TOKEN) and run this again."))

    def draft_in_progress(self, tag: str):
        if not shutil.which("gh"):
            return None
        found = subprocess.run(["gh", "release", "view", tag, "--json", "isDraft"],
                               cwd=self.repo, capture_output=True, text=True)
        if found.returncode != 0:
            return False if "release not found" in found.stderr else None
        return bool(json.loads(found.stdout).get("isDraft"))

    def _create(self, release, files) -> list:
        return ["gh", "release", "create", release.tag, *files, "--title", release.title,
                "--notes-file", release.notes, "--draft", "--verify-tag"]

    def _view(self, tag: str):
        """(release *tag* as gh shows it: assets, body, isDraft; or None, gh's
        error output when it is None)."""
        found = subprocess.run(["gh", "release", "view", tag, "--json", "assets,body,isDraft"],
                               cwd=self.repo, capture_output=True, text=True)
        if found.returncode != 0:
            return None, found.stderr.strip()
        return json.loads(found.stdout), ""

    def _assets(self, tag: str):
        """The assets of release *tag* as gh lists them, or None when it does not exist."""
        release, _error = self._view(tag)
        return release.get("assets", []) if release is not None else None

    def _check_complete(self, release, names) -> None:
        """Exit when the release, with this run's assets (*names*), lacks a
        configured platform; --force publishes anyway."""
        missing = pipeline.missing_platforms(names, release.version, release.platforms)
        if not missing:
            return
        listed = ", ".join(missing)
        if release.force:
            logger.warning(_("Publishing {0} without a build for {1} (--force).").format(release.tag, listed))
            return
        if len(missing) == 1:
            sys.exit(_("{0} has no build for {1} yet; run app-release on that platform first, or pass "
                       "--force to publish without it.").format(release.tag, listed))
        sys.exit(_("{0} has no build for {1} yet; run app-release on those platforms first, or pass "
                   "--force to publish without them.").format(release.tag, listed))

    def _keep(self) -> int:
        keep = self.config.get("keep", 0)
        if not isinstance(keep, int) or isinstance(keep, bool) or keep < 0:
            sys.exit(_("Configuration: [github] keep must be a whole number of releases, not {0!r}.").format(keep))
        return keep

    def plan(self, release) -> list:
        files = release.files()
        names = {os.path.basename(f) for f in files}
        steps = []
        existing, _error = self._view(release.tag)
        local_notes = self._read(os.path.join(self.repo, release.notes))
        if existing is not None:
            uploaded = existing.get("assets", [])
            self._check_retag(release, uploaded, names)
            present = {(a["name"], a["size"]) for a in uploaded}
            missing = [f for f in files
                       if (os.path.basename(f), os.path.getsize(os.path.join(self.repo, f))) not in present]
            if missing:
                steps.append(["gh", "release", "upload", release.tag, *missing, "--clobber"])
            names |= {a["name"] for a in uploaded}
            notes = (existing.get("body") or "").replace("\r\n", "\n")
            if release.merge_notes:
                merged = self._merged_notes(release, notes, uploaded, local_notes)
                if merged is not None:
                    notes, path = merged
                    steps.append(["gh", "release", "edit", release.tag, "--notes-file", path])
        else:
            notes = local_notes
            steps.append(self._create(release, files))
        if release.publish:
            self._check_complete(release, names)
            self._check_commits(release, notes)
            steps.append(["gh", "release", "edit", release.tag, "--draft=false"])
            steps += self._prune_steps(release)
        return steps

    @staticmethod
    def _read(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def _merged_notes(self, release, body: str, uploaded: list, local: str):
        """(the release's notes with this run's parts merged in, the
        repo-relative file they are written to), or None when unchanged.
        Notes with no marked parts (written by hand, with --notes, or before
        this tool marked them) are left alone."""
        if not pipeline.note_parts(body):
            logger.info(_("The notes of {0} have no app-release parts; leaving them as they are.").format(
                release.tag))
            return None
        sizes = {a["name"]: a["size"] for a in uploaded}
        sizes.update({a.name: os.path.getsize(a.path) for a in release.assets})
        merged = pipeline.merge_notes(body, local, pipeline.downloads_block(sizes))
        if merged.rstrip() == body.rstrip():
            return None
        path = os.path.splitext(release.notes)[0] + ".merged.md"
        with open(os.path.join(self.repo, path), "w", encoding="utf-8", newline="\n") as f:
            f.write(merged)
        return merged, path

    def _check_retag(self, release, uploaded: list, names: set) -> None:
        """Exit when --retag would move the tag under other platforms' builds
        already in the release; --force goes ahead."""
        if not (release.retag and release.remote_tag and release.remote_tag != release.commit):
            return
        others = sorted(pipeline.checksum_platforms([a["name"] for a in uploaded], release.version)
                        - pipeline.checksum_platforms(names, release.version))
        if not others:
            return
        listed = ", ".join(others)
        if release.force:
            if len(others) == 1:
                logger.warning(_("Moving {0} leaves the {1} build in the draft built from the old commit; "
                                 "going ahead (--force).").format(release.tag, listed))
            else:
                logger.warning(_("Moving {0} leaves the {1} builds in the draft built from the old commit; "
                                 "going ahead (--force).").format(release.tag, listed))
            return
        if len(others) == 1:
            sys.exit(_("Moving {0} leaves the {1} build in the draft built from the old commit; rebuild there "
                       "after the retag, or delete those assets first (--force goes ahead).").format(
                release.tag, listed))
        sys.exit(_("Moving {0} leaves the {1} builds in the draft built from the old commit; rebuild there "
                   "after the retag, or delete those assets first (--force goes ahead).").format(release.tag, listed))

    def _check_commits(self, release, notes: str) -> None:
        """Exit when the notes say a platform was built from a commit other than
        the tag's; --force publishes anyway."""
        stale = {p: c for p, c in pipeline.platform_commits(notes).items() if c and c != release.commit}
        if not stale:
            return
        listed = ", ".join(_("{0} from {1}").format(p, c[:10]) for p, c in sorted(stale.items()))
        if release.force:
            logger.warning(_("{0} is on {1}, but the notes say {2}; publishing anyway (--force).").format(
                release.tag, release.commit[:10], listed))
            return
        sys.exit(_("{0} is on {1}, but the notes say {2}; rebuild there, or pass --force to publish "
                   "anyway.").format(release.tag, release.commit[:10], listed))

    def _prune_steps(self, release) -> list:
        keep = self._keep()
        if not keep:
            return []
        listed = subprocess.run(["gh", "release", "list", "--limit", LIST_LIMIT, "--json",
                                 "tagName,isDraft,isPrerelease,isImmutable,isLatest"],
                                cwd=self.repo, capture_output=True, text=True)
        if listed.returncode != 0:
            sys.exit(_("Cannot list the GitHub releases to prune: {0}").format(listed.stderr.strip()))
        published = {r["tagName"]: r for r in json.loads(listed.stdout)
                     if re.fullmatch(rf"v{VERSION_PATTERN}", r["tagName"])
                     and not r["isDraft"] and not r["isPrerelease"]}
        # The release being published counts among those kept, though it may still be a draft.
        newest_first = sorted(set(published) | {release.tag}, key=_version_key, reverse=True)
        spare = self.config.get("prune_spare", DEFAULT_SPARE)
        steps = []
        for tag in newest_first[keep:]:
            if tag == release.tag or published[tag]["isLatest"]:
                continue
            if published[tag]["isImmutable"]:
                logger.warning(_("Not pruning {0}: it is an immutable release, whose assets cannot be deleted.").format(tag))
                continue
            for asset in self._assets(tag) or []:
                if not any(fnmatch.fnmatch(asset["name"], pattern) for pattern in spare):
                    steps.append(["gh", "release", "delete-asset", tag, asset["name"], "--yes"])
        return steps

    def dry_run_lines(self, release) -> list:
        files = release.files()
        lines = [_("GitHub: create the draft release with"),
                 "  " + command_line(self._create(release, files)),
                 _("or, for a release that exists already,"),
                 "  " + command_line(["gh", "release", "upload", release.tag, *files, "--clobber"])]
        keep = self._keep()
        if keep:
            lines.append(_("With --publish, also delete the downloads of releases older than the newest {0}.").format(keep))
        return lines

    def status_lines(self, tag: str, platforms: list) -> list:
        if not shutil.which("gh"):
            return [_("GitHub: gh is not installed, so the release cannot be read.")]
        release, error = self._view(tag)
        if release is None:
            if "release not found" in error:
                return [_("GitHub: no release for {0} yet.").format(tag)]
            return [_("GitHub: cannot read release {0}: {1}").format(tag, error or _("gh failed"))]
        names = [a["name"] for a in release.get("assets", [])]
        built = sorted(pipeline.checksum_platforms(names, tag[1:]))
        if release.get("isDraft"):
            summary = _("GitHub: release {0} is a draft, with {1} asset(s).")
        else:
            summary = _("GitHub: release {0} is published, with {1} asset(s).")
        lines = [summary.format(tag, len(names)),
                 "  " + _("Platforms built: {0}.").format(", ".join(built) or _("none"))]
        if platforms:
            missing = pipeline.missing_platforms(names, tag[1:], platforms)
            lines.append("  " + (_("Configured platforms missing: {0}.").format(", ".join(missing)) if missing
                                 else _("Every configured platform is present.")))
        parts = pipeline.platform_commits(release.get("body") or "")
        described = ", ".join(_("{0} (built from {1})").format(p, c[:10] or _("an unknown commit"))
                              for p, c in sorted(parts.items()))
        lines.append("  " + _("Notes parts: {0}.").format(described or _("none")))
        lacking = [p for p in platforms if p not in parts]
        if lacking:
            lines.append("  " + _("Configured platforms without a notes part: {0}.").format(", ".join(lacking)))
        return lines

    def reads_notes(self, steps: list) -> bool:
        return any(step[:3] == ["gh", "release", "create"]
                   or (step[:3] == ["gh", "release", "edit"] and "--notes-file" in step) for step in steps)

    def irreversible(self, steps: list) -> bool:
        return any(step[:3] == ["gh", "release", "delete-asset"] for step in steps)
