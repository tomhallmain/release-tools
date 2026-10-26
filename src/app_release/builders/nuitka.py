"""Python apps compiled with Nuitka by a ``build_exe.py`` at the repo root.

The build script writes its own record, ``build/nuitka/build_info.json``,
before it compiles, and the pip lock file beside it; the executables land
directly in ``dist/``, or on macOS as ``dist/<Product>.app`` bundles. An ``<App>Headless`` executable is rebuilt with the
script's ``--with-headless`` (which builds both) or ``--headless``. A rebuild
gets this package's memory-wait plugin in ``NUITKA_MEMORY_WAIT_PLUGIN``.
"""
import os
import re
import shutil
import sys

from app_release import platforms
from app_release.builders.base import Builder, APP, run_version
from app_release.builders.toolchains import build_python
from app_release.log import logger
from app_release.model import DIRECTORY, EXECUTABLE, Artifact, BuildRun
from app_release.versions import FileVersion, PatternVersion

BUILD_SCRIPT = "build_exe.py"
MEMORY_WAIT_ENV = "NUITKA_MEMORY_WAIT_PLUGIN"
MEMORY_WAIT_PLUGIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nuitka_memory_wait.py")
APP_VERSION_REGEX = r"^APP_VERSION\s*=\s*[\"']([^\"']+)[\"']"


class NuitkaBuilder(Builder):
    name = "nuitka"
    record_owner = APP
    default_features = {
        "oqs": {"rebuild_option": "--with-oqs", "note": "OQS (post-quantum) encryption"},
    }
    default_variant_hints = {
        "torch": {"contains": "+cu", "variant": "cuda"},
    }

    @classmethod
    def add_options(cls, parser) -> None:
        parser.add_argument("--memory-wait-plugin", action="store_true",
                            help="print the path of the Nuitka memory-wait plugin and exit "
                                 "(for a build_exe.py run directly)")

    @classmethod
    def early_action(cls, opts) -> bool:
        if getattr(opts, "memory_wait_plugin", False):
            # Read by build_exe.py from stdout: the path alone.
            print(MEMORY_WAIT_PLUGIN)
            return True
        return False

    def _script(self) -> str:
        return os.path.join(self.repo, BUILD_SCRIPT)

    def work_dir(self) -> str:
        return os.path.join(self.repo, "build")

    def stage_dir(self, tag: str) -> str:
        return os.path.join(self.repo, "dist", "release", tag)

    def record_path(self) -> str:
        return os.path.join(self.repo, "build", "nuitka", "build_info.json")

    def detect(self) -> bool:
        return os.path.isfile(self._script())

    def describe(self) -> str:
        return f"{BUILD_SCRIPT} at its root"

    def artifacts(self) -> list:
        dist = os.path.join(self.repo, "dist")
        if not os.path.isdir(dist):
            return []
        found = {}
        bundles = {}
        for name in sorted(os.listdir(dist)):
            path = os.path.join(dist, name)
            base, ext = os.path.splitext(name)
            if sys.platform == "darwin" and ext == ".app" and os.path.isdir(path):
                bundles[base] = Artifact(product=base, path=path, kind=DIRECTORY, platform=platforms.host())
                continue
            if not os.path.isfile(path):
                continue
            if sys.platform == "win32":
                if ext.lower() != ".exe":
                    continue
            elif ext or not os.access(path, os.X_OK):
                continue
            found[base] = Artifact(product=base, path=path, kind=EXECUTABLE, platform=platforms.host())
        for product, bundle in bundles.items():
            bare = found.get(product)
            if bare and os.path.getmtime(bare.path) >= os.path.getmtime(bundle.path):
                logger.warning(f"Both {bare.path} and {bundle.path} exist and the executable is newer; "
                               "packaging the app bundle. Delete whichever the build no longer makes.")
            found[product] = bundle
        return [found[product] for product in sorted(found)]

    def default_version_source(self):
        """VERSION at the root, or APP_VERSION in the module the build script
        names as ``VERSION_FILE = os.path.join("...", "version.py")``."""
        if os.path.isfile(os.path.join(self.repo, "VERSION")):
            return FileVersion(self.repo, "VERSION")
        if os.path.isfile(self._script()):
            with open(self._script(), "r", encoding="utf-8") as f:
                match = re.search(r"^VERSION_FILE\s*=\s*(.+)$", f.read(), re.M)
            parts = re.findall(r"[\"']([^\"']+)[\"']", match.group(1)) if match else []
            module = os.path.join(*parts) if parts else ""
            if module.endswith(".py") and os.path.isfile(os.path.join(self.repo, module)):
                with open(os.path.join(self.repo, module), "r", encoding="utf-8") as f:
                    if re.search(APP_VERSION_REGEX, f.read(), re.M):
                        return PatternVersion(self.repo, module, APP_VERSION_REGEX)
        return FileVersion(self.repo, "VERSION")

    def lock_files(self) -> list:
        lock = os.path.join(self.repo, "build", "nuitka", "build-requirements.lock.txt")
        return [(lock, "requirements.lock.txt")] if os.path.isfile(lock) else []

    def note_lines(self, record) -> list:
        return ["- At its first start the executable unpacks its runtime into the app's data folder; "
                "later starts of the same build reuse it."]

    def preflight(self) -> None:
        super().preflight()
        # On Windows Nuitka finds MSVC or downloads MinGW itself.
        if sys.platform == "darwin":
            # /usr/bin/cc exists without the Command Line Tools, as a stub that offers to install them.
            if run_version(["xcode-select", "-p"]) is None:
                sys.exit("Nuitka needs a C compiler: install the Xcode Command Line Tools "
                         "(xcode-select --install) and run this again.")
        elif sys.platform != "win32" and not any(shutil.which(c) for c in ("cc", "gcc", "clang")):
            sys.exit("Nuitka needs a C compiler (gcc or clang); install one and run this again.")

    def plan(self, stale: list, record) -> list:
        with open(self._script(), "r", encoding="utf-8") as f:
            source = f.read()
        app_name = self.app_config.get("app_name") or ""
        products = {a.product for a in stale}
        secondary = [p for p in products if p != app_name]
        for product in secondary:
            if product != f"{app_name}Headless":
                sys.exit(f"Cannot tell how {BUILD_SCRIPT} builds {product}; build it yourself, "
                         "or leave it out with --products.")
        target_args = [[]] if app_name in products else []
        if secondary:
            if '"--with-headless"' in source:
                # Builds the GUI executable as well.
                target_args = [["--with-headless"]]
            elif '"--headless"' in source:
                target_args.append(["--headless"])
            else:
                sys.exit(f"{BUILD_SCRIPT} has no option that builds {secondary[0]}; build it yourself, "
                         "or leave it out with --products.")
        python, reason = build_python(self.repo, getattr(self.options, "toolchain", None), record)
        logger.info(f"Building with {python} ({reason}).")
        env = dict(os.environ, **{MEMORY_WAIT_ENV: MEMORY_WAIT_PLUGIN})
        return [BuildRun(command=[python, self._script(), *args], cwd=self.repo, env=env,
                         platform=platforms.host())
                for args in target_args]
