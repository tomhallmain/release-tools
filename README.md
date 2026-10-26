# app-release

Releases an app from its checkout: rebuilds the artifacts behind HEAD, checks
the build against the checkout, stages the artifacts with lock files,
checksums and release notes, tags and pushes the build's commit, and
publishes. One copy serves every app: the build system, the version's
location and the destinations are backends chosen per app. With no
configuration, the app is a Python app compiled by a Nuitka `build_exe.py`
and released on GitHub.

| Builder | For | Record of the build |
|---------|-----|---------------------|
| `nuitka` (default) | `build_exe.py` at the root, executables in `dist/` | written by the app: `build/nuitka/build_info.json` |
| `cargo` | `Cargo.toml`; one or more targets | written by the tool |
| `command` | anything: build commands and artifact globs in configuration | written by the tool |

| Destination | Does |
|-------------|------|
| `github` (default) | a draft GitHub release through `gh`, published with `--publish`; with `keep`, publishing deletes older releases' downloads |
| `none` | nothing beyond the tag (a release made by hand, or by CI on the tag) |

It also ships `nuitka_memory_wait.py`, a Nuitka plugin that waits for free
memory before the onefile payload is compressed.

## Install

```
pipx install -e <path to this repo>
```

Needs Python 3.11 or newer and only the standard library. With `-e`, pulling
this repo updates `app-release` for every app. Without pipx,
`python -m app_release` runs it from an environment where it is installed.

## Use

```
app-release [REPO] [--builder NAME] [--destination NAME ...] [--toolchain T]
    [--bump major|minor|patch] [--variant cuda] [--products NAME ...] [--no-build]
    [--build-info FILE] [--retag] [--force] [--notes FILE] [--no-upload]
    [--publish] [--status] [--yes] [--yes-irreversible] [-- <build args>]
```

`REPO` is the app's checkout (default: the current directory). The steps and
options are in `app-release --help` and the docstring of
`src/app_release/cli.py`. Builder options: `--memory-wait-plugin` (nuitka:
print the plugin's path and exit), `--target TRIPLE` (cargo, repeatable).

### Publishing an existing build

The version is built into the executables, so a new version needs a new
build. To publish what is already built:

- `--no-build` packages a build from an ancestor of HEAD whose files differ,
  with a warning; the tag goes on the build's commit.
- `--retag` moves tag `v<version>` to the build's commit, replacing same-named
  assets and this platform's part of the notes. Use it only for a release
  nobody has downloaded: clones that fetched the old tag keep it. It refuses
  while the release holds other platforms' builds, unless `--force`.
- `--build-info FILE` restores the build record the artifacts were built
  with, after a later build rewrote it. For a Nuitka app, take it from the
  executable's unpack folder, e.g.
  `%APPDATA%\<App>\unpacked\<app>-<build id>\build_info.json`. The lock file
  cannot be restored this way.

Together: `--no-build --build-info <file> --retag --publish`.

### Releasing from several platforms

Nuitka builds only for the OS it runs on, so Windows, macOS and Linux
downloads need a machine of each. Each machine adds its build, from the same
commit, to one draft release, and the last one publishes it. Name the
platforms a release must have:

```toml
platforms = ["windows-x64", "macos-arm64", "linux-x64"]
```

1. On one machine: `app-release --bump minor --no-upload`, then push the bump
   commit. This builds and stages without tagging or uploading.
2. On every other platform: `git pull`, then `app-release --no-upload` as a
   trial. Fix platform-specific problems before anything is tagged; commit,
   push, and repeat the trials until all build from the same commit.
3. On each platform, in any order: `app-release`. The first run tags and
   creates the draft; later runs add their downloads and notes.
4. On any machine: `app-release --status`, then `app-release --publish`.

Safeguards:

- A run stops when the remote's tag is on another commit than its build, or
  when the checkout is behind its upstream. `--bump` refuses while the current
  version is a draft.
- Each run rewrites only its platform's part of the notes, the commit line and
  the downloads list, between `<!-- app-release:... -->` markers. Edits made on
  GitHub outside the markers are kept; notes without markers are left alone.
- `--publish` refuses while a configured platform is missing, or the notes
  say a platform was built from another commit than the tag's. `--force`
  publishes anyway, with a warning.
- `--status` reports the tag here and on the remote, whether the release is a
  draft, which platforms it has and lacks, and each notes part's commit. It
  changes nothing and needs no `gh` login.

Each machine needs:

- `gh`, installed and logged in (`gh auth login`).
- The build's prerequisites: for Nuitka, a C compiler (on macOS, the Xcode
  Command Line Tools). List other commands the build needs in the builder's
  table, e.g. `[nuitka] requires = ["vlc"]`. Both are checked before building.
- On macOS, the machine's architecture: Apple silicon builds `macos-arm64`
  only; `macos-x64` needs an Intel Mac.
- On Linux, an older LTS distribution: a build runs only where glibc is at
  least the build machine's.

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

All settings are listed in the docstring of `src/app_release/config.py`; each
backend's table in its module under `builders/` or `destinations/`; version
sources in `versions.py`.

GitHub limits each asset to under 2 GiB, but not the total. To delete old
downloads:

```toml
[github]
keep = 5    # on --publish, delete the assets of releases older than the newest 5
```

Checksums, lock files, notes and tags stay. Deleting cannot be undone, so it
asks again, unless `--yes-irreversible`.

Builders that write their own record (cargo, command) keep it, the log and the
staged release in `.release/` at the app's root; add it to the app's
`.gitignore`. The nuitka builder uses the app's `build/` and `dist/release/`.

### Which toolchain builds the app

Only a rebuild needs one. For `nuitka`, it is the Python that runs
`build_exe.py`, which creates `.venv-build` from it and then reuses the venv.
The first that applies:

1. `--toolchain EXE` (or `--python EXE`).
2. The app's existing `.venv-build`; a warning names a mismatch with
   `.python-version` (delete the venv to switch).
3. `.python-version` (e.g. `3.11`), found with `py -3.11` on Windows and
   `python3.11` on PATH elsewhere.
4. The major.minor Python version recorded in the last build.

For `cargo`, `--toolchain` names a rustup toolchain (`cargo +<toolchain>`);
otherwise rustup chooses (`rust-toolchain.toml`). `command` refuses it: put the
toolchain in the build command.

## What a Nuitka app must provide

- `build_exe.py` at the root, writing the executables directly to `dist/`.
- The version in `VERSION`, or as `APP_VERSION` in the module `build_exe.py`
  names in `VERSION_FILE`.
- Optionally `build/nuitka/build_info.json` (the build record) and
  `build/nuitka/build-requirements.lock.txt`.
- The memory-wait plugin, found through `NUITKA_MEMORY_WAIT_PLUGIN`, which the
  tool sets when it runs the build, else `app-release --memory-wait-plugin`.

## Tests

```
pip install -e .[test]
python -m pytest
```

The cargo end-to-end test runs only where `cargo` is installed.
