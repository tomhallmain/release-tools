"""The app's release configuration: ``release.toml`` at its root or, in a Python
app, ``[tool.release]`` in ``pyproject.toml``; never both. Neither means the
defaults, which are those of a Nuitka-built app released on GitHub.

    builder = "nuitka"                 # see builders/
    destinations = ["github"]          # see destinations/
    version = "VERSION"                # see versions.py; default: the builder's
    app_name = "SDRunner"              # default: shortest product minus a secondary suffix
    secondary_suffixes = ["Headless"]
    platforms = ["windows-x64", "macos-arm64", "linux-x64"]   # what --publish requires; default: none

    [features]                         # merged onto the builder's; false removes one
    oqs = { rebuild_option = "--with-oqs", note = "OQS (post-quantum) encryption" }

    [variant_hints]                    # merged onto the builder's; false removes one
    torch = { contains = "+cu", variant = "cuda" }

    [notes]
    extra = ["- Some line."]
    unsigned_windows = true            # the SmartScreen line for Windows executables
    unsigned_macos = true              # the Gatekeeper line for macOS builds without a Developer ID
    linux_glibc = true                 # the glibc version a Linux build needs

    [stage]
    archive_unix_executables = true    # macOS and Linux executables as .tar.gz, keeping the executable bit

    [<builder or destination name>]    # that backend's own settings

    [nuitka]                           # any builder's table
    requires = ["vlc"]                 # commands the build needs on PATH, checked before it starts
"""
import os
import sys
import tomllib

RELEASE_TOML = "release.toml"
PYPROJECT = "pyproject.toml"
KNOWN_KEYS = {"builder", "destinations", "version", "app_name", "secondary_suffixes",
              "platforms", "features", "variant_hints", "notes", "stage"}


def _read_toml(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"Cannot read {path}: {e}")


def load(repo: str):
    """(configuration, the file it came from or None)."""
    release_toml = os.path.join(repo, RELEASE_TOML)
    pyproject = os.path.join(repo, PYPROJECT)
    in_pyproject = None
    if os.path.isfile(pyproject):
        in_pyproject = _read_toml(pyproject).get("tool", {}).get("release")
    if os.path.isfile(release_toml):
        if in_pyproject is not None:
            sys.exit(f"Both {RELEASE_TOML} and [tool.release] in {PYPROJECT} configure the release; "
                     "keep one.")
        return _read_toml(release_toml), release_toml
    if in_pyproject is not None:
        return in_pyproject, pyproject
    return {}, None


def unknown_keys(config: dict, backend_names) -> list:
    """Top-level keys that are neither settings nor a backend's table, likely typos."""
    return sorted(k for k in config if k not in KNOWN_KEYS and k not in backend_names)


def merged(defaults: dict, overrides) -> dict:
    """*defaults* with *overrides* applied; an override of false removes the entry."""
    result = dict(defaults)
    for name, value in (overrides or {}).items():
        if value is False:
            result.pop(name, None)
        else:
            result[name] = value
    return result


def platforms(config: dict) -> list:
    """The platforms a release must have before --publish, e.g. "macos-arm64";
    [] when unset (a single-platform app needs none)."""
    value = config.get("platforms", [])
    if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
        sys.exit(f"Configuration: platforms must be a list of platform names such as \"linux-x64\", "
                 f"not {value!r}.")
    return value


def archive_unix(config: dict) -> bool:
    """Whether macOS and Linux executables are staged as .tar.gz ([stage])."""
    value = (config.get("stage") or {}).get("archive_unix_executables", True)
    if not isinstance(value, bool):
        sys.exit(f"Configuration: [stage] archive_unix_executables must be true or false, not {value!r}.")
    return value


def app_name(config: dict, products) -> str:
    """The app's name: configured, or the shortest product name without a
    secondary suffix (Weidr for both Weidr and WeidrHeadless)."""
    if config.get("app_name"):
        return config["app_name"]
    name = min(products, key=len)
    for suffix in config.get("secondary_suffixes", ["Headless"]):
        if suffix and name.endswith(suffix) and name != suffix:
            return name[:-len(suffix)]
    return name
