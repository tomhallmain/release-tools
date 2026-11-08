"""app_release.log's timing and run logs, and app_release.platforms' names."""

import os
import sys

import pytest

from app_release import log, platforms
from app_release.lib.translations import _


def test_timed_logs_the_duration_of_a_step(caplog, monkeypatch):
    monkeypatch.setattr(log, "duration", lambda seconds: "5s")
    with caplog.at_level("INFO", logger="app_release"):
        with log.timed("Uploading"):
            pass
    assert caplog.messages[0] == _("{0}...").format("Uploading")
    assert caplog.messages[-1] == _("{0} done in {1}").format("Uploading", "5s")


def test_timed_logs_a_failed_step(caplog, monkeypatch):
    monkeypatch.setattr(log, "duration", lambda seconds: "5s")
    with caplog.at_level("INFO", logger="app_release"):
        with pytest.raises(SystemExit):
            with log.timed("Uploading"):
                raise SystemExit("broken")
    assert caplog.messages[-1] == _("{0} failed after {1}").format("Uploading", "5s")


@pytest.fixture
def run_log(tmp_path, monkeypatch):
    """A run log for app "App" under tmp_path/logs; its folder."""
    monkeypatch.setenv(log.LOG_DIR_ENV, str(tmp_path / "logs"))
    folder = log.setup("App")
    yield folder
    log.finish()


def test_the_log_folder_can_be_set(tmp_path, monkeypatch):
    monkeypatch.setenv(log.LOG_DIR_ENV, str(tmp_path))
    assert log.log_root() == str(tmp_path)


def test_the_default_log_folder_is_the_user_s_state_folder(tmp_path, monkeypatch):
    monkeypatch.delenv(log.LOG_DIR_ENV)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert log.log_root() == os.path.join(str(tmp_path), "app-release", "logs")


def test_a_run_log_is_named_for_the_version(run_log, tmp_path):
    log.logger.info("before the version")
    assert not os.path.exists(run_log)
    log.name_after("1.2.0")
    log.logger.info("after it")
    log.keep_copy_in(str(tmp_path / "stage"))
    log.finish()
    (name,) = os.listdir(run_log)
    assert name.endswith("-v1.2.0.log")
    text = open(os.path.join(run_log, name), encoding="utf-8").read()
    assert "before the version" in text and "after it" in text and _("Log: {0}").format(os.path.join(run_log, name)) in text
    assert os.listdir(tmp_path / "stage" / "logs") == [name]
    assert log.logger.handlers == []


def test_a_run_that_stops_before_the_version_is_still_logged(run_log):
    log.logger.error("no version")
    log.finish()
    (name,) = os.listdir(run_log)
    assert "-v" not in name
    assert "no version" in open(os.path.join(run_log, name), encoding="utf-8").read()


def test_a_command_s_output_goes_to_the_console_and_the_log(run_log, capsys):
    log.name_after("1.0.0")
    code = log.run_logged([sys.executable, "-c",
                           "import sys; print('to stdout'); print('to stderr', file=sys.stderr); sys.exit(3)"])
    assert code == 3
    out = capsys.readouterr().out
    assert "to stdout" in out and "to stderr" in out
    log.finish()
    (name,) = os.listdir(run_log)
    text = open(os.path.join(run_log, name), encoding="utf-8").read()
    assert "to stdout" in text and "to stderr" in text


def _old_logs(folder, count):
    os.makedirs(folder)
    names = [f"202001{day:02d}-120000-v1.0.{day}.log" for day in range(1, count + 1)]
    for name in names:
        open(os.path.join(folder, name), "w").close()
    return names


def test_cleanup_keeps_the_newest_logs_whatever_their_age(tmp_path):
    folder = str(tmp_path / "App")
    names = _old_logs(folder, 5)
    deleted = log.cleanup(folder, keep_days=30, keep_at_least=2)
    assert sorted(os.path.basename(p) for p in deleted) == names[:3]
    assert sorted(os.listdir(folder)) == names[3:]


def test_cleanup_is_off_unless_keep_days_is_set(tmp_path, monkeypatch):
    assert log.KEEP_DAYS is None
    names = _old_logs(str(tmp_path / "logs" / "App"), 5 + log.KEEP_AT_LEAST)
    monkeypatch.setenv(log.LOG_DIR_ENV, str(tmp_path / "logs"))
    log.setup("App")
    log.finish()
    assert set(names) <= set(os.listdir(tmp_path / "logs" / "App"))


def test_duration():
    assert log.duration(3 * 3600 + 4 * 60 + 5) == _("{0}h {1:02d}m {2:02d}s").format(3, 4, 5)
    assert log.duration(4 * 60 + 5) == _("{0}m {1:02d}s").format(4, 5)
    assert log.duration(5.4) == _("{0}s").format(5)


@pytest.mark.parametrize("triple, name", [
    ("x86_64-pc-windows-msvc", "windows-x64"),
    ("x86_64-unknown-linux-gnu", "linux-x64"),
    ("aarch64-apple-darwin", "macos-arm64"),
    ("i686-pc-windows-gnu", "windows-x86"),
    ("wasm32-unknown-unknown", "wasm32-unknown-unknown"),
])
def test_target_triples(triple, name):
    assert platforms.from_triple(triple) == name


def test_windows_triples():
    assert platforms.triple_is_windows("x86_64-pc-windows-msvc")
    assert not platforms.triple_is_windows("x86_64-unknown-linux-gnu")


def test_host_has_os_and_arch():
    assert platforms.host().count("-") == 1
