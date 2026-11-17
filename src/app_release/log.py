"""The tool's log: the console and a file per run, with each step timed.

Run logs are kept outside the checkout, in one folder per app under
log_root(), as ``<YYYYmmdd-HHMMSS>-v<version>.log``. The output of each
command run through run_logged() (the build, the publishing steps) goes into
the run's log as well as to the console. finish() copies the log into the
release's staging folder, under ``logs/``, which is not uploaded.

Old logs are deleted only when KEEP_DAYS is set: those older than that many
days, past an app's newest KEEP_AT_LEAST.
"""
import codecs
import contextlib
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta

from app_release.lib.translations import _

logger = logging.getLogger("app_release")

LOG_DIR_ENV = "APP_RELEASE_LOG_DIR"
STAMP_FORMAT = "%Y%m%d-%H%M%S"
#: Days an app's run logs are kept; None keeps them all.
KEEP_DAYS = None
#: How many of an app's newest run logs are kept, however old.
KEEP_AT_LEAST = 10

_run_log = None
_handlers = []
_copy_dirs = []


class _LevelPrefix(logging.Filter):
    def filter(self, record):
        record.level_prefix = "" if record.levelno == logging.INFO else f"{record.levelname}: "
        return True


def log_root() -> str:
    """APP_RELEASE_LOG_DIR if set, else ``app-release/logs`` under %LOCALAPPDATA%
    on Windows and $XDG_STATE_HOME (default ~/.local/state) elsewhere."""
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(os.path.join("~", "AppData", "Local"))
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser(os.path.join("~", ".local", "state"))
    return os.path.join(base, "app-release", "logs")


class RunLog(logging.Handler):
    """The run's log file. What is written before name_after() is held in
    memory, so the file gets the version in its name without a rename."""

    def __init__(self, folder: str, stamp: str):
        super().__init__()
        self.folder = folder
        self.stamp = stamp
        self.path = os.path.join(folder, f"{stamp}.log")
        self._pending = []
        self._named = False
        self._file = None
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.addFilter(_LevelPrefix())

    def _write(self, text: str) -> None:
        if not self._named:
            self._pending.append(text)
            return
        if self._file is None:
            os.makedirs(self.folder, exist_ok=True)
            self._file = open(self.path, "a", encoding="utf-8", newline="")
        self._file.write(text)
        self._file.flush()

    def _open_as(self, path: str) -> None:
        self.path = path
        self._named = True
        pending, self._pending = self._pending, []
        for text in pending:
            self._write(text)

    def name_after(self, version: str) -> None:
        self.acquire()
        try:
            if not self._named:
                self._open_as(os.path.join(self.folder, f"{self.stamp}-v{version}.log"))
        finally:
            self.release()

    def write_output(self, text: str) -> None:
        """Write a command's output as it came."""
        self.acquire()
        try:
            self._write(text)
        finally:
            self.release()

    def emit(self, record):
        try:
            self._write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)

    def close(self):
        global _run_log
        self.acquire()
        try:
            if not self._named:
                self._open_as(self.path)
            if self._file is not None:
                self._file.close()
                self._file = None
        finally:
            self.release()
        if _run_log is self:
            _run_log = None
        super().close()


def setup(app: str) -> str:
    """Log to the console and to a new run log in *app*'s folder under
    log_root(); returns that folder."""
    global _run_log
    folder = os.path.join(log_root(), app or "app")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("[%(asctime)s] %(level_prefix)s%(message)s", "%H:%M:%S"))
    console.addFilter(_LevelPrefix())
    _run_log = RunLog(folder, time.strftime(STAMP_FORMAT))
    _handlers[:] = [console, _run_log]
    _copy_dirs.clear()
    for handler in _handlers:
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    if KEEP_DAYS is not None:
        for path in cleanup(folder, KEEP_DAYS, KEEP_AT_LEAST):
            logger.info(_("Deleted the old run log {0}").format(path))
    return folder


def name_after(version: str) -> None:
    """Give this run's log file *version* in its name."""
    if _run_log is not None:
        _run_log.name_after(version)


def keep_copy_in(folder: str) -> None:
    """Have finish() copy this run's log into *folder*/logs."""
    if _run_log is not None and folder not in _copy_dirs:
        _copy_dirs.append(folder)


def finish() -> None:
    """Name the log file on the console, close it, copy it where keep_copy_in()
    asked, and remove the handlers setup() added."""
    run_log = _run_log
    if run_log is None:
        return
    logger.info(_("Log: {0}").format(run_log.path))
    logger.removeHandler(run_log)
    run_log.close()
    for folder in _copy_dirs:
        target = os.path.join(folder, "logs")
        try:
            os.makedirs(target, exist_ok=True)
            shutil.copy2(run_log.path, target)
        except OSError as e:
            logger.warning(_("Could not copy the log into {0}: {1}").format(target, e))
    _copy_dirs.clear()
    for handler in _handlers:
        logger.removeHandler(handler)
    _handlers.clear()


def _log_date(path: str) -> datetime:
    """The time in a run log's name, or the file's modification time."""
    try:
        return datetime.strptime(os.path.basename(path)[:15], STAMP_FORMAT)
    except ValueError:
        return datetime.fromtimestamp(os.path.getmtime(path))


def cleanup(folder: str, keep_days: int, keep_at_least: int) -> list:
    """Delete the run logs in *folder* older than *keep_days*, keeping the newest
    *keep_at_least* whatever their age; returns the deleted paths."""
    try:
        paths = [os.path.join(folder, name) for name in os.listdir(folder) if name.endswith(".log")]
    except OSError:
        return []
    paths.sort(key=_log_date, reverse=True)
    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = []
    for path in paths[keep_at_least:]:
        if _log_date(path) < cutoff:
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(_("Could not delete the old run log {0}: {1}").format(path, e))
            else:
                deleted.append(path)
    return deleted


def _echo(text: str) -> None:
    if not text:
        return
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        sys.stdout.write(text.encode(encoding, "replace").decode(encoding))
    sys.stdout.flush()
    if _run_log is not None:
        _run_log.write_output(text)


def run_logged(command: list, cwd=None, env=None) -> int:
    """Run *command*, showing its output (stdout and stderr together) on the
    console and copying it into the run's log; returns its exit code. Its stdin
    stays the terminal, for any question it asks."""
    env = dict(os.environ if env is None else env)
    # Python children write through at once, and in UTF-8, when piped.
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        # os.read returns what is there, so a prompt without a newline shows at once.
        while chunk := os.read(process.stdout.fileno(), 65536):
            _echo(decoder.decode(chunk))
        _echo(decoder.decode(b"", final=True))
        return process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()


def duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return _("{0}h {1:02d}m {2:02d}s").format(hours, minutes, seconds)
    if minutes:
        return _("{0}m {1:02d}s").format(minutes, seconds)
    return _("{0}s").format(seconds)


@contextlib.contextmanager
def timed(step: str):
    """Log how long *step* took, or after how long it failed."""
    started = time.monotonic()
    logger.info(_("{0}...").format(step))
    try:
        yield
    except BaseException:
        logger.error(_("{0} failed after {1}").format(step, duration(time.monotonic() - started)))
        raise
    logger.info(_("{0} done in {1}").format(step, duration(time.monotonic() - started)))


def command_line(args: list) -> str:
    """*args* as the platform's shell would take them, for logs and printouts."""
    return subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
