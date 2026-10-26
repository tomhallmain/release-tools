"""GitHub releases through the GitHub CLI (gh, logged in): a draft release
with every staged asset, published with --publish.

    [github]
    keep = 5                     # prune: keep the downloads of the newest 5 releases
    prune_spare = ["SHA256SUMS-*", "*-requirements.lock.txt"]   # assets pruning never deletes

With ``keep``, a run that publishes then deletes the assets of older published
``v<version>`` releases, except those matching ``prune_spare``; the release,
its notes and its tag stay. Drafts, pre-releases, the release GitHub marks
latest and the one being published are never pruned. Immutable releases are
skipped: GitHub refuses to delete their assets. Deleting cannot be undone, so
it needs the second confirmation (``--yes-irreversible``).
"""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys

from app_release.destinations.base import Destination
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
            sys.exit("gh (GitHub CLI) is not installed; install it, run `gh auth login`, "
                     "or pass --no-upload.")
        # Checked before anything runs, so a missing login cannot stop the run after the tag is pushed.
        if subprocess.run(["gh", "auth", "status"], cwd=self.repo, capture_output=True).returncode != 0:
            sys.exit("gh is not logged in to GitHub; run `gh auth login` (or set GH_TOKEN) and run this again.")

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

    def _assets(self, tag: str):
        """The assets of release *tag* as gh lists them, or None when it does not exist."""
        found = subprocess.run(["gh", "release", "view", tag, "--json", "assets"],
                               cwd=self.repo, capture_output=True, text=True)
        return json.loads(found.stdout)["assets"] if found.returncode == 0 else None

    def _keep(self) -> int:
        keep = self.config.get("keep", 0)
        if not isinstance(keep, int) or isinstance(keep, bool) or keep < 0:
            sys.exit(f"Configuration: [github] keep must be a whole number of releases, not {keep!r}.")
        return keep

    def plan(self, release) -> list:
        files = release.files()
        steps = []
        uploaded = self._assets(release.tag)
        if uploaded is not None:
            present = {(a["name"], a["size"]) for a in uploaded}
            missing = [f for f in files
                       if (os.path.basename(f), os.path.getsize(os.path.join(self.repo, f))) not in present]
            if missing:
                steps.append(["gh", "release", "upload", release.tag, *missing, "--clobber"])
        else:
            steps.append(self._create(release, files))
        if release.publish:
            steps.append(["gh", "release", "edit", release.tag, "--draft=false"])
            steps += self._prune_steps(release)
        return steps

    def _prune_steps(self, release) -> list:
        keep = self._keep()
        if not keep:
            return []
        listed = subprocess.run(["gh", "release", "list", "--limit", LIST_LIMIT, "--json",
                                 "tagName,isDraft,isPrerelease,isImmutable,isLatest"],
                                cwd=self.repo, capture_output=True, text=True)
        if listed.returncode != 0:
            sys.exit(f"Cannot list the GitHub releases to prune: {listed.stderr.strip()}")
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
                logger.warning(f"Not pruning {tag}: it is an immutable release, whose assets cannot be deleted.")
                continue
            for asset in self._assets(tag) or []:
                if not any(fnmatch.fnmatch(asset["name"], pattern) for pattern in spare):
                    steps.append(["gh", "release", "delete-asset", tag, asset["name"], "--yes"])
        return steps

    def dry_run_lines(self, release) -> list:
        files = release.files()
        lines = ["GitHub: create the draft release with",
                 "  " + command_line(self._create(release, files)),
                 "or, for a release that exists already,",
                 "  " + command_line(["gh", "release", "upload", release.tag, *files, "--clobber"])]
        keep = self._keep()
        if keep:
            lines.append(f"With --publish, also delete the downloads of releases older than the newest {keep}.")
        return lines

    def reads_notes(self, steps: list) -> bool:
        # Only `gh release create` reads the notes; an existing release keeps its own.
        return any(step[:3] == ["gh", "release", "create"] for step in steps)

    def irreversible(self, steps: list) -> bool:
        return any(step[:3] == ["gh", "release", "delete-asset"] for step in steps)
