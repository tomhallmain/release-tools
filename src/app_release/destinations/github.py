"""GitHub releases through the GitHub CLI (gh, logged in): a draft release
with every staged asset, published with --publish."""
import json
import os
import shutil
import subprocess
import sys

from app_release.destinations.base import Destination
from app_release.log import command_line


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

    def _create(self, release, files) -> list:
        return ["gh", "release", "create", release.tag, *files, "--title", release.title,
                "--notes-file", release.notes, "--draft", "--verify-tag"]

    def plan(self, release) -> list:
        files = release.files()
        steps = []
        found = subprocess.run(["gh", "release", "view", release.tag, "--json", "assets"],
                               cwd=self.repo, capture_output=True, text=True)
        if found.returncode == 0:
            uploaded = {(a["name"], a["size"]) for a in json.loads(found.stdout)["assets"]}
            missing = [f for f in files
                       if (os.path.basename(f), os.path.getsize(os.path.join(self.repo, f))) not in uploaded]
            if missing:
                steps.append(["gh", "release", "upload", release.tag, *missing, "--clobber"])
        else:
            steps.append(self._create(release, files))
        if release.publish:
            steps.append(["gh", "release", "edit", release.tag, "--draft=false"])
        return steps

    def dry_run_lines(self, release) -> list:
        files = release.files()
        return ["GitHub: create the draft release with",
                "  " + command_line(self._create(release, files)),
                "or, for a release that exists already,",
                "  " + command_line(["gh", "release", "upload", release.tag, *files, "--clobber"])]

    def reads_notes(self, steps: list) -> bool:
        # Only `gh release create` reads the notes; an existing release keeps its own.
        return any(step[:3] == ["gh", "release", "create"] for step in steps)
