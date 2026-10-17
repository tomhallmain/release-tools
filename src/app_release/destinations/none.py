"""No host: the release is staged, tagged and pushed, and published by hand or
by CI started by the tag."""
from app_release.destinations.base import Destination


class NoDestination(Destination):
    name = "none"

    def plan(self, release) -> list:
        return []

    def dry_run_lines(self, release) -> list:
        return ["none: nothing to publish beyond the tag."]
