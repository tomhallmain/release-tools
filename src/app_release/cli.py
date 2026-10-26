"""Release an app: rebuild what is behind, stage the artifacts, tag the build's
commit and publish.

Run on the machine that builds, from any directory:

    app-release [REPO] [--builder NAME] [--destination NAME ...] [--toolchain T]
        [--bump major|minor|patch] [--variant cuda] [--products NAME ...] [--no-build]
        [--build-info FILE] [--retag] [--force] [--notes FILE] [--no-upload]
        [--publish] [--status] [--yes] [--yes-irreversible] [-- <build args>]

REPO is the app's checkout (default: the current directory). Its
``release.toml``, or ``[tool.release]`` in its ``pyproject.toml``, picks the
builder, the destinations and the version's location; without either, the app
is taken to be built by a Nuitka ``build_exe.py`` and released on GitHub.

``--bump`` first raises that part of the version (1.4.2 -> 2.0.0, 1.5.0 or
1.4.3) and commits the version file alone, once tag v<version> exists: a rerun
before the new version is tagged keeps it. It needs a clean checkout, and the
artifacts are then rebuilt, since they embed the version. It refuses while
the current version's release is still a draft (other platforms may be adding
to it), unless ``--force``.

Before bumping or building, it fetches the branch's upstream and stops when
the checkout is behind it ("pull first"; ``--force`` goes ahead).

First it rebuilds the artifacts that are behind the checkout's HEAD, with the
arguments after ``--`` added to each build command (e.g. ``-- --jobs=4``), plus
the option of each feature the last build had (Nuitka: ``--with-oqs``). It
builds nothing while tracked files have uncommitted changes, nor when the
machine lacks what the build needs (the builder's ``requires`` commands;
Nuitka: a C compiler). ``--no-build``
skips this step; ``--build-info FILE`` first puts back the build record the
artifacts were built with.

Then it checks the build against the checkout (version, commit or same files,
no uncommitted changes, artifacts not older than the build, an existing tag on
the build's commit, both here and on ``--remote``, asset sizes); ``--force``
packages despite failed checks. Another machine's tag on the remote is what
counts: a local tag missing or moved is made to match it when it is on the
build's commit. The remote must be reachable, except with ``--no-upload``.
It stages each artifact as ``<Product>-<version>[-<os>-<arch>][-<variant>]<ext>``
with the lock files, a SHA256SUMS file per platform and release notes, unless
``--notes`` names a file to use. A rerun regenerates the notes only while the
file is as last generated, so edits to it are kept.

Last, it tags the build's commit v<version>, pushes the current branch to
``--remote`` when the remote's copy of it lacks that commit, pushes the tag, and
publishes to each destination; that each destination is ready (gh installed
and logged in) is checked before building. A GitHub release that exists
already, from another platform's run, gets this run's assets and its part of
the notes merged into the release's (see destinations/github.py). Before
notes are created or changed, it shows them to approve, edit in $EDITOR
(Notepad on Windows when unset) or abort, then asks once to run the commands;
``--yes`` skips both prompts. Steps that cannot be undone (publishing to a
package registry, deleting old releases' downloads) ask again, unless
``--yes-irreversible``. Steps already done are skipped, so a rerun resumes;
``--retag`` moves an existing tag to the build's commit, and ``--publish``
publishes a GitHub draft, once every configured ``platforms`` entry is in it
and built from the tag's commit. ``--no-upload`` stops after staging and
prints the commands.

``--status`` only reports: the tag here and on ``--remote``, and each
destination's release for the checkout's version (draft or not, its
platforms, those configured but missing, the commit of each notes part).

Each step is timed, and the log goes to the console and to a file in the app's
build folder (Nuitka: build/logs/; otherwise .release/logs/).
"""
import argparse
import os
import subprocess
import sys
import time

from app_release import __version__, config as configuration, pipeline, vcs
from app_release.builders import BUILDERS, DEFAULT as DEFAULT_BUILDER
from app_release.destinations import DESTINATIONS, DEFAULT as DEFAULT_DESTINATIONS
from app_release.log import command_line, duration, logger, setup as setup_logging, timed
from app_release.model import ReleasePlan
from app_release.versions import BUMP_PARTS, bump, from_config as version_from_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app-release", description=__doc__.split("\n\n")[0])
    parser.add_argument("repo", nargs="?", default=".",
                        help="the app's checkout (default: the current directory)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--builder", choices=sorted(BUILDERS),
                        help="build system, instead of the configured one (default: nuitka)")
    parser.add_argument("--destination", action="append", choices=sorted(DESTINATIONS),
                        help="where to publish, instead of the configured ones (default: github); repeatable")
    parser.add_argument("--toolchain", "--python", dest="toolchain", metavar="T",
                        help="what runs the build: the Python interpreter (nuitka), a rustup "
                             "toolchain (cargo)")
    parser.add_argument("--bump", choices=BUMP_PARTS,
                        help="raise this part of the version and commit it once the current one is tagged")
    parser.add_argument("--variant", help="suffix for a build variant, e.g. cuda")
    parser.add_argument("--products", nargs="+", help="package only these products (names without extension)")
    parser.add_argument("--no-build", action="store_true", help="package the artifacts as they are, without rebuilding")
    parser.add_argument("--build-info", metavar="FILE",
                        help="the build record the artifacts were built with, to put back in place")
    parser.add_argument("--retag", action="store_true",
                        help="move tag v<version> to the build's commit when it points elsewhere")
    parser.add_argument("--force", action="store_true", help="package even when checks fail")
    parser.add_argument("--notes", help="release notes file to use instead of the generated one")
    parser.add_argument("--no-upload", action="store_true", help="stage the assets and print the commands only")
    parser.add_argument("--publish", action="store_true", help="publish the release instead of leaving a draft")
    parser.add_argument("--status", action="store_true",
                        help="report the tag and the release of the checkout's version, and change nothing")
    parser.add_argument("--remote", default="origin", help="git remote to push the tag to (default: origin)")
    parser.add_argument("--yes", action="store_true", help="approve the release notes and upload without asking first")
    parser.add_argument("--yes-irreversible", action="store_true",
                        help="also run the steps that cannot be undone without asking")
    for builder in BUILDERS.values():
        builder.add_options(parser)
    return parser


def _builder(repo, config, opts):
    name = opts.builder or config.get("builder") or DEFAULT_BUILDER
    if name not in BUILDERS:
        sys.exit(f"Configuration: builder {name!r} is not one of {', '.join(sorted(BUILDERS))}.")
    builder = BUILDERS[name](repo, config, opts)
    if not builder.detect():
        others = [other for other, cls in BUILDERS.items()
                  if other != name and cls(repo, config, opts).detect()]
        hint = f" It looks like a {others[0]} project: set builder = \"{others[0]}\"." if others else ""
        sys.exit(f"{repo} has no {builder.describe()}, which builder {name!r} needs.{hint}")
    return builder


def _destinations(repo, config, opts) -> list:
    names = opts.destination or config.get("destinations") or DEFAULT_DESTINATIONS
    unknown = [n for n in names if n not in DESTINATIONS]
    if unknown:
        sys.exit(f"Configuration: destination {unknown[0]!r} is not one of {', '.join(sorted(DESTINATIONS))}.")
    return [DESTINATIONS[n](repo, config) for n in names]


def _status(repo, tag, remote, destinations, platforms) -> None:
    """Print where tag *tag* is and what each destination holds for it; runs
    only git and gh commands that read."""
    local = vcs.tagged_commit(repo, tag)
    on_remote = vcs.remote_tagged_commit(repo, tag, remote)
    print(f"Tag {tag}: here {'at ' + local[:10] if local else 'absent'}; on {remote} "
          + ("unknown (cannot reach it)" if on_remote is None
             else f"at {on_remote[:10]}" if on_remote else "absent") + ".")
    if local and on_remote and local != on_remote:
        print(f"  The tags differ: {remote}'s is the one that counts; fetch it (git fetch {remote} --tags --force).")
    if platforms:
        print(f"Configured platforms: {', '.join(platforms)}.")
    for destination in destinations:
        for line in destination.status_lines(tag, platforms):
            print(line)


def _check_up_to_date(repo, opts) -> None:
    """Exit when the branch's upstream has commits the checkout lacks, such as
    a version bump pushed from another machine."""
    upstream = vcs.upstream(repo)
    if not upstream:
        logger.info("The checkout's branch has no upstream; not checking that it is up to date.")
        return
    behind = vcs.commits_behind_upstream(repo)
    if behind is None:
        if not opts.no_upload:
            sys.exit(f"Cannot fetch {upstream} to check that the checkout is up to date.")
        logger.warning(f"Cannot fetch {upstream}: not checking that the checkout is up to date.")
    elif behind:
        message = f"{upstream} has {behind} commit(s) this checkout lacks; pull first."
        if not opts.force:
            sys.exit(message + " (--force goes ahead without them.)")
        logger.warning(message + " Going ahead without them (--force).")


def _check_no_draft(tag, destinations, opts) -> None:
    """Exit when --bump would start a new version while release *tag* is a
    draft that other platforms may still be adding to."""
    for destination in destinations:
        draft = destination.draft_in_progress(tag)
        if draft is None:
            message = f"Cannot tell whether {tag} is a draft release on {destination.name}"
            if not opts.no_upload:
                sys.exit(message + "; not bumping.")
            logger.warning(message + "; bumping anyway (--no-upload).")
        elif draft:
            message = (f"{tag} is a draft release in progress on {destination.name}; build it on the "
                       "remaining platforms, or publish it, before bumping")
            if not opts.force:
                sys.exit(message + " (--force bumps anyway).")
            logger.warning(message + "; bumping anyway (--force).")


def _confirm(question: str) -> bool:
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    build_args = []
    if "--" in argv:
        build_args = argv[argv.index("--") + 1:]
        argv = argv[:argv.index("--")]
    opts = _parser().parse_args(argv)
    for builder_cls in BUILDERS.values():
        if builder_cls.early_action(opts):
            return

    repo = os.path.abspath(opts.repo)
    if not os.path.isdir(repo):
        sys.exit(f"{repo} is not a folder.")
    config, config_path = configuration.load(repo)
    builder = _builder(repo, config, opts)
    destinations = _destinations(repo, config, opts)
    logger.info(f"Log: {setup_logging(builder.log_dir())}")
    logger.info(f"app-release {__version__} on {repo}: builder {builder.name}, "
                f"destinations {', '.join(d.name for d in destinations)}"
                + (f", configured in {config_path}" if config_path else ", default configuration"))
    for key in configuration.unknown_keys(config, set(BUILDERS) | set(DESTINATIONS)):
        logger.warning(f"Configuration: {key!r} is not a setting this version reads; a typo?")
    features = configuration.merged(builder.default_features, config.get("features"))
    variant_hints = configuration.merged(builder.default_variant_hints, config.get("variant_hints"))

    version_source = version_from_config(repo, config.get("version")) or builder.default_version_source()
    version = version_source.read()
    platforms = configuration.platforms(config)
    if opts.status:
        _status(repo, f"v{version}", opts.remote, destinations, platforms)
        return
    artifacts = builder.artifacts()
    if opts.products:
        missing = [p for p in opts.products if p not in {a.product for a in artifacts}]
        if missing:
            sys.exit(f"No artifact for: {', '.join(missing)}")
        artifacts = [a for a in artifacts if a.product in opts.products]
    if not artifacts:
        sys.exit(f"No artifacts found for builder {builder.name}; build first.")
    if opts.notes and not os.path.isfile(opts.notes):
        sys.exit(f"--notes: {opts.notes} not found.")
    if not opts.no_upload:
        # Also checked before publishing; this saves a long build that could not be uploaded.
        for destination in destinations:
            destination.check_ready()
    _check_up_to_date(repo, opts)

    if opts.bump and vcs.tagged_commit(repo, f"v{version}"):
        _check_no_draft(f"v{version}", destinations, opts)
    if opts.bump:
        version = bump(repo, version_source, version, opts.bump, opts.no_build)
    tag = f"v{version}"
    remote_tag = vcs.remote_tagged_commit(repo, tag, opts.remote)
    if remote_tag is None:
        if not opts.no_upload:
            sys.exit(f"Cannot reach the git remote {opts.remote} to check tag {tag} there.")
        logger.warning(f"Cannot reach the git remote {opts.remote}: not checking tag {tag} there.")

    app_name = configuration.app_name(config, [a.product for a in artifacts])
    builder.app_config = {**config, "app_name": app_name}
    if opts.build_info:
        builder.restore_record(opts.build_info, artifacts)
    if not opts.no_build:
        record = builder.read_record()
        if pipeline.rebuild_stale(repo, version, artifacts, builder, record,
                                  pipeline.feature_args(record, features, build_args), features):
            artifacts = [a for a in builder.artifacts() if not opts.products or a.product in opts.products]
    record = builder.read_record()
    limits = [d.max_asset_bytes for d in destinations if d.max_asset_bytes]
    errors, warnings = pipeline.check(repo, version, tag, record, artifacts, opts.variant, variant_hints,
                                      min(limits) if limits else None, opts.retag, opts.no_build,
                                      remote_tag, opts.remote)
    for message in warnings:
        logger.warning(message)
    for message in errors:
        logger.error(message)
    if errors and not opts.force:
        sys.exit("Nothing packaged; fix the errors above or pass --force.")
    commit = (record.commit if record else None) or vcs.git(repo, "rev-parse", "HEAD")
    if not commit:
        sys.exit("Cannot tell which commit the build is from.")

    out_dir = builder.stage_dir(tag)
    with timed(f"Staging the assets in {out_dir}"):
        downloads, sums = pipeline.stage(out_dir, app_name, version, opts.variant, artifacts,
                                         builder.lock_files())
    title = f"{app_name} {version}"
    notes = os.path.abspath(opts.notes) if opts.notes else os.path.join(out_dir, "RELEASE_NOTES.md")
    if not opts.notes:
        text = pipeline.release_notes(repo, title, record, commit, artifacts, downloads,
                                      builder.note_lines(record), features, config.get("notes"))
        if not pipeline.write_generated_notes(notes, text):
            logger.info(f"Keeping the edited release notes in {notes}; delete the file to regenerate them.")
    logger.info(f"Staged in {out_dir}; release notes: {notes}")

    release = ReleasePlan(repo=repo, tag=tag, commit=commit, remote=opts.remote, title=title,
                          assets=downloads + sums,
                          notes=os.path.relpath(notes, repo).replace(os.sep, "/"),
                          publish=opts.publish, retag=opts.retag, remote_tag=remote_tag,
                          platforms=platforms, force=opts.force, merge_notes=not opts.notes)
    if opts.no_upload:
        moved = " (git tag -f, and git push --force for the tag)" if opts.retag else ""
        print(f"\nTo publish, from {repo}, tag {commit[:10]} as {tag}{moved}, push the branch with "
              "that commit and the tag, then:")
        for destination in destinations:
            for line in destination.dry_run_lines(release):
                print(line)
        return

    steps, reads_notes, irreversible = pipeline.publish_steps(release, destinations)
    if not steps:
        logger.info(f"{title} is already published with these assets.")
        return
    confirm = not opts.yes and sys.stdin.isatty()
    if confirm and not opts.notes and reads_notes:
        for path in dict.fromkeys(step[step.index("--notes-file") + 1] for step in steps if "--notes-file" in step):
            pipeline.approve_notes(os.path.join(repo, path))
    print(f"\nIn {repo}:")
    for step in steps:
        print("  " + command_line(step))
    if not opts.yes:
        if not confirm:
            sys.exit("Not run: pass --yes to upload without a terminal to confirm in.")
        if not _confirm("Run these?"):
            sys.exit("Not run.")
    if irreversible and not opts.yes_irreversible:
        if not sys.stdin.isatty():
            sys.exit(f"Not run: steps for {', '.join(irreversible)} cannot be undone; "
                     "pass --yes-irreversible to run them without a terminal.")
        if not _confirm(f"Steps for {', '.join(irreversible)} cannot be undone. Go ahead?"):
            sys.exit("Not run.")
    for step in steps:
        with timed(f"> {command_line(step)}"):
            if subprocess.run(step, cwd=repo).returncode != 0:
                sys.exit("Stopped: the command above failed. Running this again resumes from there.")
    if opts.publish:
        logger.info(f"Published {title}.")
    elif any(d.name == "github" for d in destinations):
        logger.info(f"{title} is a draft release; publish it on GitHub, or run this again with --publish.")


def renamed(argv=None) -> None:
    """The ``nuitka-release`` entry point."""
    sys.exit("nuitka-release is now app-release: run app-release with the same arguments "
             "(app-release --memory-wait-plugin for the plugin's path).")


def run(argv=None) -> None:
    """The console entry point: main() with each outcome logged and timed."""
    started = time.monotonic()
    try:
        main(argv)
    except SystemExit as e:
        if e.code not in (None, 0):
            if isinstance(e.code, str):
                logger.error(e.code)
            logger.error(f"Stopped after {duration(time.monotonic() - started)}")
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        logger.error(f"Interrupted after {duration(time.monotonic() - started)}")
        sys.exit(1)
    except BaseException:
        logger.exception(f"Failed after {duration(time.monotonic() - started)}")
        sys.exit(1)
    logger.info(f"Done in {duration(time.monotonic() - started)}")
