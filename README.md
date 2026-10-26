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
| `github` (default) | a draft GitHub release through `gh`, published with `--publish`; with `keep`, publishing deletes older releases' downloads |
| `none` | nothing beyond the tag (a release made by hand, or by CI on the tag) |

It also ships the Nuitka plugin `nuitka_memory_wait.py`, which waits for free
memory before the onefile payload is compressed.

## Install

```
pipx install -e <path to this repo>
```

Needs Python 3.11 or newer, for the tool only: pipx gives it its own
interpreter, and it uses only the standard library. With `-e`, pulling this
repo updates the command for every app. It installs `app-release`. Without
pipx, `python -m app_release` runs it from an environment where it is
installed.

## Use

```
app-release [REPO] [--builder NAME] [--destination NAME ...] [--toolchain T]
    [--bump major|minor|patch] [--variant cuda] [--products NAME ...] [--no-build]
    [--build-info FILE] [--retag] [--force] [--notes FILE] [--no-upload]
    [--publish] [--status] [--yes] [--yes-irreversible] [-- <build args>]
```

`REPO` is the app's checkout (default: the current directory). The steps and
options are described in the docstring of `src/app_release/cli.py` and by
`app-release --help`.

### Publishing an existing build

The version is built into the executables, so a new version always needs a
new build. To publish what is already built:

- `--no-build` packages a build from an ancestor of HEAD whose files differ,
  with a warning; the tag goes on the build's commit.
- `--retag` moves tag `v<version>` to the build's commit. Same-named assets
  of an existing GitHub release are replaced, and this platform's part of its
  notes is rewritten. Use it only for a release nobody has downloaded: clones
  that fetched the old tag keep it. It refuses while the release holds other
  platforms' builds, which would be left built from the old commit, unless
  `--force`.
- `--build-info FILE` puts back the build record the artifacts were built
  with, after a later build run rewrote it. For a Nuitka app, take it from
  the executable's unpack folder, e.g.
  `%APPDATA%\<App>\unpacked\<app>-<build id>\build_info.json`; the rewritten
  lock file cannot be restored this way.

Together: `--no-build --build-info <file> --retag --publish`.

Builder options: `--memory-wait-plugin` (nuitka: print the plugin's path and
exit), `--target TRIPLE` (cargo, repeatable).

### Releasing from several platforms

A builder such as Nuitka builds only for the OS it runs on, so a release with
Windows, macOS and Linux downloads is built on a machine of each. Every
machine adds its build to one GitHub draft release for `v<version>`, built
from the same commit, and the last one publishes it.

Name the platforms a release must have in the configuration:

```toml
platforms = ["windows-x64", "macos-arm64", "linux-x64"]
```

Then:

1. On one machine: `app-release --bump minor --no-upload`. This commits the
   new version and builds and stages it without tagging or uploading. Push
   the bump commit.
2. On every other platform: `git pull`, then `app-release --no-upload` as a
   trial build. Fix anything platform-specific now, before the version is
   tagged anywhere: commit, push, and repeat the trial on the platforms
   already done, until all build from the same commit.
3. On each platform, in any order: `app-release`. The first run tags the
   commit and creates the draft; each later run adds its downloads and its
   part of the release notes.
4. On any machine: `app-release --status`, then `app-release --publish`.

What keeps the release consistent:

- The tag on the remote is the reference. A run stops when the remote's tag
  is on another commit than its build, and copies the remote's tag when it
  has none locally.
- A run stops before building when the checkout is behind its upstream
  ("pull first"), and `--bump` refuses while the current version is still a
  draft.
- Each run rewrites only its own platform's part of the notes, the commit
  line and the downloads list, between `<!-- app-release:... -->` markers.
  Edits made on GitHub outside the markers are kept. Notes with no markers
  (written by hand or given with `--notes`) are left alone.
- `--publish` refuses while a configured platform has no build in the
  release, or the notes say a platform was built from a commit other than
  the tag's. `--force` publishes anyway, with a warning.
- `--status` reports the tag here and on the remote, whether the release is a
  draft, which platforms it has and lacks, and the commit of each notes part.
  It changes nothing and needs no `gh` login.

Each machine needs:

- `gh`, installed and logged in (`gh auth login`); checked before building.
- The app's build prerequisites: for Nuitka, a C compiler (on macOS, the Xcode
  Command Line Tools); commands the build needs can be listed in the builder's
  table, e.g. `[nuitka] requires = ["vlc"]`, and are checked before building.
- On macOS, a build is for the machine's architecture only: Apple silicon gives
  `macos-arm64`; `macos-x64` needs an Intel Mac.
- On Linux, a build runs only where glibc is at least the build machine's, so
  build on an older LTS distribution.

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
builder's or destination's own table is described in its module under
`src/app_release/builders/` or `src/app_release/destinations/`, and the
version sources in `versions.py`.

GitHub sets no limit on the total size of releases, only under 2 GiB per
asset. To delete old downloads anyway:

```toml
[github]
keep = 5    # on --publish, delete the assets of releases older than the newest 5
```

The checksums, lock files, notes and tags stay. Deleting cannot be undone,
so it asks again, unless `--yes-irreversible`.

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

- `docs/TODO.md`: what is pending, open issues, and what waits for an app
  that needs it.
- `docs/app_requirements.md`: what a Nuitka app provides, per-app status, and
  moving an app from its own copy of the script to this tool.
- `docs/nuitka_build_notes.md`: Nuitka findings that bear on releasing.

## Tests

```
pip install -e .[test]
python -m pytest
```

The cargo end-to-end test runs only where `cargo` is installed.
