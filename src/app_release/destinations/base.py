"""The destination seam: where a release goes after its tag is pushed."""
from abc import ABC, abstractmethod


class Destination(ABC):
    name = ""
    # The largest asset it accepts, or None.
    max_asset_bytes = None
    # False when a published version cannot be replaced (package registries);
    # such a destination gets its own confirmation.
    reversible = True

    def __init__(self, repo: str, config: dict):
        self.repo = repo
        self.config = config.get(self.name) or {}

    def accepts(self, asset) -> bool:
        """Whether *asset* (a model.Asset) goes to this destination."""
        return True

    def check_ready(self) -> None:
        """Exit, before anything runs, when the destination cannot be reached
        (its CLI missing, not logged in)."""

    def draft_in_progress(self, tag: str):
        """Whether release *tag* exists here unpublished, so other platforms
        may still be adding to it; None when it cannot tell."""
        return False

    def status_lines(self, tag: str, platforms: list) -> list:
        """What --status reports for release *tag* here, reading only; *platforms*
        are those a release must have."""
        return []

    @abstractmethod
    def plan(self, release) -> list:
        """The commands, run in the repo, that put *release* (a ReleasePlan)
        here, leaving out what is done already."""

    @abstractmethod
    def dry_run_lines(self, release) -> list:
        """What --no-upload prints for this destination."""

    def reads_notes(self, steps: list) -> bool:
        """Whether *steps* use the release notes, so they need approval first."""
        return False

    def irreversible(self, steps: list) -> bool:
        """Whether *steps* include one that cannot be undone, so they get their
        own confirmation."""
        return bool(steps) and not self.reversible
