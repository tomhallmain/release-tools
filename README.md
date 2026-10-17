# app-release

Releases an app from its checkout: rebuilds the artifacts that are behind
HEAD, checks the build against the checkout, stages the artifacts with lock
files, checksums and release notes, tags the build's commit, pushes, and
publishes. One copy serves every app, whatever its language: the build
system, the version's location and the destinations are backends, chosen per
app. With no configuration, an app is taken to be a Python app compiled by a
Nuitka `build_exe.py` and released on GitHub.

| Builder | For | Record of the build |
|---------|-----|---------------------|
| `nuitka` (default) | `build_exe.py` at the root, executables in `dist/` | written by the app: `build/nuitka/build_info.json` |
| `cargo` | `Cargo.toml`; one or more targets | written by the tool |
| `command` | anything: build commands and artifact globs in configuration | written by the tool |

| Destination | Does |
|-------------|------|
| `github` (default) | a draft GitHub release through `gh`, published with `--publish` |
| `none` | nothing beyond the tag (a release made by hand, or by CI on the tag) |

It also ships the Nuitka plugin `nuitka_memory_wait.py`, which waits for free
memory before the onefile payload is compressed.

## Install

```
pipx install -e <path to this repo>
```

Needs Python 3.11 or newer, for the tool only: pipx gives it its own
interpreter, and it uses only the standard library. With `-e`, pulling this
repo updates the command for every app. It installs `app-release`, and
`nuitka-release` as a second name for apps whose `build_exe.py` asks for that
one. Without pipx, `python -m app_release` runs it from an environment where it
is installed.

## Use

```
app-release [REPO] [--builder NAME] [--destination NAME ...] [--toolchain T]
    [--bump major|minor|patch] [--variant cuda] [--products NAME ...] [--no-build]
    [--build-info FILE] [--retag] [--force] [--notes FILE] [--no-upload]
    [--publish] [--yes] [--yes-irreversible] [-- <build args>]
```

`REPO` is the app's checkout (default: the current directory). The steps and
options are described in the docstring of `src/app_release/cli.py` and by
`app-release --help`; `docs/spec.md` has the detail.

Builder options: `--memory-wait-plugin` (nuitka: print the plugin's path and
exit), `--target TRIPLE` (cargo, repeatable).

## Configuration

`release.toml` at the app's root, or `[tool.release]` in its `pyproject.toml`;
not both. Every setting is optional:

```toml
builder = "cargo"
destinations = ["github"]
version = { toml = { file = "Cargo.toml", key = "package.version" } }
app_name = "mytool"

[cargo]
targets = ["x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"]
```

The full list is in the docstring of `src/app_release/config.py`; each
builder's own table is described in its module under
`src/app_release/builders/`, and the version sources in `versions.py`.

A builder that writes its own record (cargo, command) keeps it, the log and
the staged release in `.release/` at the app's root; add that folder to the
app's `.gitignore`. The nuitka builder uses the app's `build/` and
`dist/release/`, as before.

### Which toolchain builds the app

Only a rebuild needs one. For `nuitka`, the Python interpreter that runs
`build_exe.py`, which creates the app's `.venv-build` from it and reuses the
venv once it exists. The first of these that applies:

1. `--toolchain EXE` (or `--python EXE`).
2. The app's existing `.venv-build`. A warning names any mismatch with
   `.python-version`; delete the venv to switch.
3. `.python-version` in the app's repo (e.g. `3.11`), found with `py -3.11` on
   Windows and `python3.11` on PATH elsewhere.
4. The major.minor Python version recorded in the last build.

For `cargo`, `--toolchain` names a rustup toolchain (`cargo +<toolchain>`);
otherwise rustup's own choice applies (`rust-toolchain.toml`). `command`
refuses it: put the toolchain in the build command.

## What a Nuitka app must provide

`docs/app_requirements.md` has the detail and the per-app status. In short:
`build_exe.py` at the root; the version in `VERSION` or as `APP_VERSION` in the
module `build_exe.py` names in `VERSION_FILE`; executables directly in `dist/`;
optionally `build/nuitka/build_info.json` and
`build/nuitka/build-requirements.lock.txt`; and the memory-wait plugin found
through `NUITKA_MEMORY_WAIT_PLUGIN`, which the tool sets when it runs the
build, else `app-release --memory-wait-plugin`.

## Docs

- `docs/spec.md`: what a release does, step by step, naming, open issues, TODO.
- `docs/spec_backends.md`: the backends design, and how its steps were done.
- `docs/app_requirements.md`: what a Nuitka app provides, per-app status, and
  moving an app from its own copy of the script to this tool.
- `docs/nuitka_build_notes.md`: Nuitka findings that bear on releasing.

## Tests

```
pip install -e .[test]
python -m pytest
```

The cargo end-to-end test runs only where `cargo` is installed.
