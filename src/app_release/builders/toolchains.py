"""The Python toolchain: which interpreter runs an app's build script.

build_exe.py creates the app's build venv (.venv-build) from the interpreter
running it, and reuses the venv once it exists; that fixes the Python version
the executables are built with. In order, the first that applies:

1. ``--toolchain`` (or ``--python``), as given.
2. The app's existing .venv-build, which the build would reuse anyway.
3. The version in the app's .python-version, e.g. ``3.11``.
4. The major.minor Python version recorded in the last build record.

A version from 3 or 4 is found with the py launcher (``py -3.11``) on Windows,
and as ``python3.11`` on PATH elsewhere.
"""
import os
import re
import shutil
import subprocess
import sys

from app_release.log import logger

VENV_DIR = ".venv-build"
PIN_FILE = ".python-version"
_PROBE = "import sys; print(sys.executable); print('%d.%d.%d' % sys.version_info[:3])"


def _venv_python(venv_dir: str) -> str:
    if sys.platform == "win32":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _major_minor(text) -> str:
    """"3.11" from text such as "3.11.9", "3.11" or "cpython-3.11", or ""."""
    match = re.search(r"(\d+)\.(\d+)", text or "")
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def pinned_version(repo: str) -> str:
    """The major.minor version in the app's .python-version, or ""."""
    path = os.path.join(repo, PIN_FILE)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                version = _major_minor(line)
                if not version:
                    sys.exit(f"{path}: cannot read a Python version from {line!r}.")
                return version
    return ""


def probe(command: list):
    """(sys.executable, "X.Y.Z") of the interpreter *command* starts, or None
    when it does not run."""
    try:
        result = subprocess.run([*command, "-c", _PROBE], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = result.stdout.split("\n")
    if result.returncode != 0 or len(lines) < 2 or not lines[0].strip():
        return None
    return lines[0].strip(), lines[1].strip()


def _find_version(version: str):
    """probe() of an installed Python *version* ("3.11"), or None."""
    if sys.platform == "win32":
        return probe(["py", f"-{version}"])
    found = shutil.which(f"python{version}")
    return probe([found]) if found else None


def build_python(repo: str, explicit=None, record=None):
    """(interpreter path, why it was chosen) for running *repo*'s build script;
    *record* is the last BuildRecord, or None. Exits when none is found."""
    pin = pinned_version(repo)
    if explicit:
        found = probe([explicit])
        if not found:
            sys.exit(f"--toolchain: {explicit} does not run.")
        return found[0], f"--toolchain, Python {found[1]}"

    venv_python = _venv_python(os.path.join(repo, VENV_DIR))
    if os.path.isfile(venv_python):
        found = probe([venv_python])
        if not found:
            sys.exit(f"{venv_python} does not run; delete {VENV_DIR} or pass --toolchain.")
        if pin and _major_minor(found[1]) != pin:
            logger.warning(f"{VENV_DIR} has Python {found[1]}, {PIN_FILE} says {pin}; the build reuses "
                           f"{VENV_DIR}. Delete it to build with {pin}.")
        return found[0], f"the app's {VENV_DIR}, Python {found[1]}"

    recorded = record.toolchain.get("Python") if record else None
    for version, source in ((pin, PIN_FILE), (_major_minor(recorded), "the last build")):
        if not version:
            continue
        found = _find_version(version)
        if not found:
            sys.exit(f"{source} asks for Python {version}, which is not installed here; "
                     "install it or pass --toolchain.")
        return found[0], f"Python {found[1]}, from {source}"

    sys.exit(f"Cannot tell which Python to build with: the app has no {VENV_DIR}, no {PIN_FILE} "
             "and no earlier build. Add a .python-version (e.g. 3.11) or pass --toolchain.")
