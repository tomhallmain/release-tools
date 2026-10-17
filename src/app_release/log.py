"""The tool's log: the console and a file per run, with each step timed."""
import contextlib
import logging
import os
import shlex
import subprocess
import sys
import time

logger = logging.getLogger("app_release")


class _LevelPrefix(logging.Filter):
    def filter(self, record):
        record.level_prefix = "" if record.levelno == logging.INFO else f"{record.levelname}: "
        return True


def setup(log_dir: str) -> str:
    """Log to the console and to a file for this run in *log_dir*; returns the file's path."""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, f"app_release-{time.strftime('%Y%m%d-%H%M%S')}.log")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("[%(asctime)s] %(level_prefix)s%(message)s", "%H:%M:%S"))
    log_file = logging.FileHandler(path, encoding="utf-8")
    log_file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    for handler in (console, log_file):
        handler.addFilter(_LevelPrefix())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return path


def duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


@contextlib.contextmanager
def timed(step: str):
    """Log how long *step* took, or after how long it failed."""
    started = time.monotonic()
    logger.info(f"{step}...")
    try:
        yield
    except BaseException:
        logger.error(f"{step} failed after {duration(time.monotonic() - started)}")
        raise
    logger.info(f"{step} done in {duration(time.monotonic() - started)}")


def command_line(args: list) -> str:
    """*args* as the platform's shell would take them, for logs and printouts."""
    return subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
