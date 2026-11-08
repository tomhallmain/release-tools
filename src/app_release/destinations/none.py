"""No host: the release is staged, tagged and pushed, and published by hand or
by CI started by the tag."""
from app_release.destinations.base import Destination
from app_release.lib.translations import _


class NoDestination(Destination):
    name = "none"

    def plan(self, release) -> list:
        return []

    def dry_run_lines(self, release) -> list:
        return [_("{0}: nothing to publish beyond the tag.").format(self.name)]
