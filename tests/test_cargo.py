"""The cargo builder, against a fake target folder; one test builds for real
when cargo is installed."""

import os
import shutil

import pytest

from app_release import platforms, vcs
from app_release.builders.cargo import CargoBuilder
from app_release.model import EXECUTABLE
from helpers import Options, checkout_git

MANIFEST = '[package]\nname = "tool"\nversion = "0.3.1"\nedition = "2021"\n'


def _builder(tmp_path, manifest=MANIFEST, table=None, **options):
    (tmp_path / "Cargo.toml").write_text(manifest, encoding="utf-8")
    return CargoBuilder(str(tmp_path), {"cargo": table or {}}, Options(**options))


def _binary(tmp_path, target, name, windows):
    folder = tmp_path / "target" / (target or "") / "release"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + (".exe" if windows else ""))
    path.write_bytes(b"x")
    return str(path)


def test_version_from_the_manifest(tmp_path):
    assert _builder(tmp_path).default_version_source().read() == "0.3.1"


def test_host_build(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    builder = _builder(tmp_path)
    path = _binary(tmp_path, None, "tool", platforms.host_is_windows())
    assert [(a.product, a.path, a.kind, a.platform) for a in builder.artifacts()] == [
        ("tool", path, EXECUTABLE, platforms.host())]
    assert [r.command for r in builder.plan([], None)] == [["cargo", "build", "--release", "--bin", "tool"]]


def test_configured_targets_and_bins(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    table = {"targets": ["x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"], "bins": ["a", "b"]}
    builder = _builder(tmp_path, table=table, toolchain="stable")
    _binary(tmp_path, "x86_64-pc-windows-msvc", "a", True)
    _binary(tmp_path, "x86_64-unknown-linux-gnu", "b", False)
    assert sorted((a.product, a.platform) for a in builder.artifacts()) == [
        ("a", "windows-x64"), ("b", "linux-x64")]
    runs = builder.plan([], None)
    assert [r.command for r in runs] == [
        ["cargo", "+stable", "build", "--release", "--bin", "a", "--bin", "b", "--target", "x86_64-pc-windows-msvc"],
        ["cargo", "+stable", "build", "--release", "--bin", "a", "--bin", "b", "--target", "x86_64-unknown-linux-gnu"]]
    assert [r.platform for r in runs] == ["windows-x64", "linux-x64"]


def test_target_option_replaces_the_configured_ones(tmp_path):
    builder = _builder(tmp_path, table={"targets": ["x86_64-pc-windows-msvc"]},
                       target=["aarch64-apple-darwin"])
    assert [r.command[-1] for r in builder.plan([], None)] == ["aarch64-apple-darwin"]


def test_binaries_from_bin_tables(tmp_path):
    manifest = MANIFEST + '\n[[bin]]\nname = "one"\n\n[[bin]]\nname = "two"\n'
    assert _builder(tmp_path, manifest=manifest)._bins() == ["one", "two"]


def test_lock_file(tmp_path):
    builder = _builder(tmp_path)
    assert builder.lock_files() == []
    (tmp_path / "Cargo.lock").write_text("", encoding="utf-8")
    assert builder.lock_files() == [(str(tmp_path / "Cargo.lock"), "Cargo.lock")]


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo is not installed")
def test_build_end_to_end(tmp_path, monkeypatch):
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)
    monkeypatch.setattr(vcs, "git", checkout_git())
    builder = _builder(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text('fn main() { println!("built"); }\n', encoding="utf-8")
    builder.build(builder.plan([], None), [], "0.3.1", {})
    artifacts = builder.artifacts()
    assert [a.product for a in artifacts] == ["tool"]
    record = builder.read_record()
    assert record.version == "0.3.1" and "rustc" in record.toolchain
    assert os.path.getmtime(artifacts[0].path) >= record.written_at
