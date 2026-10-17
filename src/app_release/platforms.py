"""Platform names in asset names: ``<os>-<arch>``, e.g. ``windows-x64``."""
import platform
import sys

_OS = {"win32": "windows", "darwin": "macos"}
_ARCH = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64", "i686": "x86", "i386": "x86"}
# The OS part of a target triple (arch-vendor-os[-env]) as asset names spell it.
_TRIPLE_OS = {"windows": "windows", "darwin": "macos", "apple": "macos", "linux": "linux",
              "freebsd": "freebsd"}


def host() -> str:
    os_name = _OS.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    return f"{os_name}-{_ARCH.get(machine, machine)}"


def host_is_windows() -> bool:
    return sys.platform == "win32"


def from_triple(triple: str) -> str:
    """``x86_64-pc-windows-msvc`` -> ``windows-x64``; an unrecognized triple as given."""
    parts = triple.split("-")
    arch = _ARCH.get(parts[0])
    os_name = next((_TRIPLE_OS[p] for p in parts[1:] if p in _TRIPLE_OS), None)
    return f"{os_name}-{arch}" if arch and os_name else triple


def triple_is_windows(triple: str) -> bool:
    return "-windows-" in f"{triple}-"
