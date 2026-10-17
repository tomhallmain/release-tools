"""app_release.log's timing and app_release.platforms' names."""

import pytest

from app_release import log, platforms


def test_timed_logs_the_duration_of_a_step(caplog):
    with caplog.at_level("INFO", logger="app_release"):
        with log.timed("Uploading"):
            pass
    assert caplog.messages[0] == "Uploading..."
    assert caplog.messages[-1].startswith("Uploading done in ")


def test_timed_logs_a_failed_step(caplog):
    with caplog.at_level("INFO", logger="app_release"):
        with pytest.raises(SystemExit):
            with log.timed("Uploading"):
                raise SystemExit("broken")
    assert caplog.messages[-1].startswith("Uploading failed after ")


def test_duration():
    assert log.duration(3 * 3600 + 4 * 60 + 5) == "3h 04m 05s"


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
