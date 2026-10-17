from app_release.destinations.github import GitHubRelease
from app_release.destinations.none import NoDestination

# By the name configuration uses: destinations = ["<name>", ...].
DESTINATIONS = {cls.name: cls for cls in (GitHubRelease, NoDestination)}
DEFAULT = [GitHubRelease.name]
