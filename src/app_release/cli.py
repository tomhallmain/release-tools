"""Release an app: rebuild what is behind, stage the artifacts, tag the build's
commit and publish.

REPO is the app's checkout (default: the current directory). Its
``release.toml`` or ``[tool.release]`` in ``pyproject.toml`` picks the builder,
the destinations and the version's location; without either, the app is a
Nuitka ``build_exe.py`` app released on GitHub. Run on the machine that builds.

1. Early checks: each destination is ready (``gh`` installed and logged in;
   skipped with ``--no-upload``), and the checkout is not behind its upstream
   after a fetch (``--force`` goes ahead).
2. ``--bump`` raises that part of the version (1.4.2 -> 2.0.0, 1.5.0, 1.4.3)
   and commits the version file, once tag v<version> exists. It needs a clean
   checkout, and refuses while that version's release is a draft other
   platforms may still be adding to, unless ``--force``.
3. Rebuild the artifacts behind HEAD, adding the arguments after ``--`` and the
   option of each feature the last build had (Nuitka: ``--with-oqs``). Nothing
   is built with uncommitted changes or a missing prerequisite (the builder's
   ``requires``; Nuitka: a C compiler). ``--no-build`` skips this step;
   ``--build-info FILE`` first restores the record the artifacts were built with.
4. Check the build: version, commit or same files, no uncommitted changes,
   artifacts newer than the build, asset sizes, and tag v<version> on the
   build's commit both here and on ``--remote``. The remote's tag counts: a
   missing or stale local tag is moved to match it, and an unreachable remote
   stops a run that uploads. ``--force`` packages despite failed checks.
5. Stage each artifact as ``<Product>-<version>[-<os>-<arch>][-<variant>]<ext>``,
   with the lock files, a SHA256SUMS file per platform and release notes
   (``--notes FILE`` gives your own). Edits to the generated notes file are
   kept across reruns. ``--no-upload`` stops here and prints the commands.
6. Tag the build's commit, push the branch when the remote lacks the commit,
   push the tag, and publish. A GitHub release that exists already gets this
   run's assets and its part of the notes merged in. ``--retag`` moves a tag
   on another commit; ``--publish`` publishes a GitHub draft once every
   configured ``platforms`` entry is in it, built from the tag's commit.

Notes about to be created or changed are shown to approve, edit in $EDITOR
(Notepad on Windows when unset) or abort; then it asks once to run the
commands. ``--yes`` skips both prompts. Steps that cannot be undone (publishing
to a package registry, deleting old releases' downloads) ask again, unless
``--yes-irreversible``. Steps already done are skipped, so a rerun resumes.

``--status`` only reports: the tag here and on ``--remote``, and each
destination's release (draft or not, platforms present and missing, the commit
of each notes part).

Each step is timed. The log, with the output of the build and of the
publishing commands, goes to the console and to a file per run in the app's
folder under app-release's log folder (``APP_RELEASE_LOG_DIR``, else
%LOCALAPPDATA%\\app-release\\logs on Windows, ~/.local/state/app-release/logs
elsewhere), named for the time and the version. A copy goes to the staging
folder's ``logs/``, which is not uploaded.
"""
import argparse
import os
import sys
import time

from app_release import __version__, config as configuration, pipeline, record as records, vcs
from app_release.builders import BUILDERS, DEFAULT as DEFAULT_BUILDER
from app_release.builders.base import APP
from app_release.destinations import DESTINATIONS, DEFAULT as DEFAULT_DESTINATIONS
from app_release import log
from app_release.lib.translations import _
from app_release.log import command_line, duration, logger, run_logged, timed
from app_release.model import ReleasePlan
from app_release.versions import BUMP_PARTS, bump, from_config as version_from_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app-release", description=_(
        "Release an app: rebuild what is behind, stage the artifacts, tag the build's commit and publish."))
    parser.add_argument("repo", nargs="?", default=".",
                        help=_("the app's checkout (default: the current directory)"))
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--builder", choices=sorted(BUILDERS),
                        help=_("build system, instead of the configured one (default: nuitka)"))
    parser.add_argument("--destination", action="append", choices=sorted(DESTINATIONS),
                        help=_("where to publish, instead of the configured ones (default: github); repeatable"))
    parser.add_argument("--toolchain", "--python", dest="toolchain", metavar="T",
                        help=_("what runs the build: the Python interpreter (nuitka), a rustup "
                               "toolchain (cargo)"))
    parser.add_argument("--bump", choices=BUMP_PARTS,
                        help=_("raise this part of the version and commit it once the current one is tagged"))
    parser.add_argument("--variant", help=_("suffix for a build variant, e.g. cuda"))
    parser.add_argument("--products", nargs="+", help=_("package only these products (names without extension)"))
    parser.add_argument("--no-build", action="store_true", help=_("package the artifacts as they are, without rebuilding"))
    parser.add_argument("--build-info", metavar="FILE",
                        help=_("the build record the artifacts were built with, to put back in place"))
    parser.add_argument("--retag", action="store_true",
                        help=_("move tag v<version> to the build's commit when it points elsewhere"))
    parser.add_argument("--force", action="store_true", help=_("package even when checks fail"))
    parser.add_argument("--notes", help=_("release notes file to use instead of the generated one"))
    parser.add_argument("--no-upload", action="store_true", help=_("stage the assets and print the commands only"))
    parser.add_argument("--publish", action="store_true", help=_("publish the release instead of leaving a draft"))
    parser.add_argument("--status", action="store_true",
                        help=_("report the tag and the release of the checkout's version, and change nothing"))
    parser.add_argument("--remote", default="origin", help=_("git remote to push the tag to (default: origin)"))
    parser.add_argument("--yes", action="store_true", help=_("approve the release notes and upload without asking first"))
    parser.add_argument("--yes-irreversible", action="store_true",
                        help=_("also run the steps that cannot be undone without asking"))
    for builder in BUILDERS.values():
        builder.add_options(parser)
    return parser


def _builder(repo, config, opts):
    name = opts.builder or config.get("builder") or DEFAULT_BUILDER
    if name not in BUILDERS:
        sys.exit(_("Configuration: builder {0!r} is not one of {1}.").format(name, ", ".join(sorted(BUILDERS))))
    builder = BUILDERS[name](repo, config, opts)
    if not builder.detect():
        others = [other for other, cls in BUILDERS.items()
                  if other != name and cls(repo, config, opts).detect()]
        message = _("{0} has no {1}, which builder {2!r} needs.").format(repo, builder.describe(), name)
        if others:
            message += " " + _("It looks like a {0} project: set builder = \"{0}\".").format(others[0])
        sys.exit(message)
    return builder


def _destinations(repo, config, opts) -> list:
    names = opts.destination or config.get("destinations") or DEFAULT_DESTINATIONS
    unknown = [n for n in names if n not in DESTINATIONS]
    if unknown:
        sys.exit(_("Configuration: destination {0!r} is not one of {1}.").format(
            unknown[0], ", ".join(sorted(DESTINATIONS))))
    return [DESTINATIONS[n](repo, config) for n in names]


def _status(repo, tag, remote, destinations, platforms) -> None:
    """Print where tag *tag* is and what each destination holds for it; runs
    only git and gh commands that read."""
    local = vcs.tagged_commit(repo, tag)
    on_remote = vcs.remote_tagged_commit(repo, tag, remote)
    here = _("at {0}").format(local[:10]) if local else _("absent")
    there = (_("unknown (cannot reach it)") if on_remote is None
             else _("at {0}").format(on_remote[:10]) if on_remote else _("absent"))
    print(_("Tag {0}: here {1}; on {2} {3}.").format(tag, here, remote, there))
    if local and on_remote and local != on_remote:
        print("  " + _("The tags differ: {0}'s is the one that counts; fetch it ({1}).").format(
            remote, f"git fetch {remote} --tags --force"))
    if platforms:
        print(_("Configured platforms: {0}.").format(", ".join(platforms)))
    for destination in destinations:
        for line in destination.status_lines(tag, platforms):
            print(line)


def _check_up_to_date(repo, opts) -> None:
    """Exit when the branch's upstream has commits the checkout lacks, such as
    a version bump pushed from another machine."""
    upstream = vcs.upstream(repo)
    if not upstream:
        logger.info(_("The checkout's branch has no upstream; not checking that it is up to date."))
        return
    behind = vcs.commits_behind_upstream(repo)
    if behind is None:
        if not opts.no_upload:
            sys.exit(_("Cannot fetch {0} to check that the checkout is up to date.").format(upstream))
        logger.warning(_("Cannot fetch {0}: not checking that the checkout is up to date.").format(upstream))
    elif behind:
        if not opts.force:
            sys.exit(_("{0} has {1} commit(s) this checkout lacks; pull first. (--force goes ahead without "
                       "them.)").format(upstream, behind))
        logger.warning(_("{0} has {1} commit(s) this checkout lacks; pull first. Going ahead without them "
                         "(--force).").format(upstream, behind))


def _check_no_draft(tag, destinations, opts) -> None:
    """Exit when --bump would start a new version while release *tag* is a
    draft that other platforms may still be adding to."""
    for destination in destinations:
        draft = destination.draft_in_progress(tag)
        if draft is None:
            if not opts.no_upload:
                sys.exit(_("Cannot tell whether {0} is a draft release on {1}; not bumping.").format(
                    tag, destination.name))
            logger.warning(_("Cannot tell whether {0} is a draft release on {1}; bumping anyway "
                             "(--no-upload).").format(tag, destination.name))
        elif draft:
            if not opts.force:
                sys.exit(_("{0} is a draft release in progress on {1}; build it on the remaining platforms, "
                           "or publish it, before bumping (--force bumps anyway).").format(tag, destination.name))
            logger.warning(_("{0} is a draft release in progress on {1}; build it on the remaining platforms, "
                             "or publish it, before bumping; bumping anyway (--force).").format(
                tag, destination.name))


def _confirm(question: str) -> bool:
    # English answers work in every language.
    yes = _("yes").lower()
    return input(_("{0} [y/N] ").format(question)).strip().lower() in ("y", "yes", yes[:1], yes)


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
        sys.exit(_("{0} is not a folder.").format(repo))
    config, config_path = configuration.load(repo)
    builder = _builder(repo, config, opts)
    destinations = _destinations(repo, config, opts)
    log.setup(os.path.basename(repo))
    names = ", ".join(d.name for d in destinations)
    if config_path:
        logger.info(_("app-release {0} on {1}: builder {2}, destinations {3}, configured in {4}").format(
            __version__, repo, builder.name, names, config_path))
    else:
        logger.info(_("app-release {0} on {1}: builder {2}, destinations {3}, default configuration").format(
            __version__, repo, builder.name, names))
    for key in configuration.unknown_keys(config, set(BUILDERS) | set(DESTINATIONS)):
        logger.warning(_("Configuration: {0!r} is not a setting this version reads; a typo?").format(key))
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
            sys.exit(_("No artifact for: {0}").format(", ".join(missing)))
        artifacts = [a for a in artifacts if a.product in opts.products]
    if not artifacts:
        sys.exit(_("No artifacts found for builder {0}; build first.").format(builder.name))
    if opts.notes and not os.path.isfile(opts.notes):
        sys.exit(_("--notes: {0} not found.").format(opts.notes))
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
    log.name_after(version)
    remote_tag = vcs.remote_tagged_commit(repo, tag, opts.remote)
    if remote_tag is None:
        if not opts.no_upload:
            sys.exit(_("Cannot reach the git remote {0} to check tag {1} there.").format(opts.remote, tag))
        logger.warning(_("Cannot reach the git remote {0}: not checking tag {1} there.").format(opts.remote, tag))

    app_name = configuration.app_name(config, [a.product for a in artifacts])
    builder.app_config = {**config, "app_name": app_name}
    if opts.build_info:
        builder.restore_record(opts.build_info, artifacts)
    if not opts.no_build:
        record = builder.read_record()
        if pipeline.rebuild_stale(repo, version, artifacts, builder, record,
                                  pipeline.feature_args(record, features, build_args), features):
            artifacts = [a for a in builder.artifacts() if not opts.products or a.product in opts.products]
            # The app's record is written before the compile. A clock step
            # backward during the compile leaves a finished executable older
            # than that file.
            if builder.record_owner == APP:
                records.date_before_artifacts(builder.record_path(), artifacts)
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
        sys.exit(_("Nothing packaged; fix the errors above or pass --force."))
    commit = (record.commit if record else None) or vcs.git(repo, "rev-parse", "HEAD")
    if not commit:
        sys.exit(_("Cannot tell which commit the build is from."))

    out_dir = builder.stage_dir(tag)
    log.keep_copy_in(out_dir)
    with timed(_("Staging the assets in {0}").format(out_dir)):
        downloads, sums = pipeline.stage(out_dir, app_name, version, opts.variant, artifacts,
                                         builder.lock_files(), configuration.archive_unix(config))
    for message in pipeline.staged_too_large(downloads, min(limits) if limits else None):
        if not opts.no_upload:
            sys.exit(message)
        logger.warning(message)
    title = f"{app_name} {version}"
    notes = os.path.abspath(opts.notes) if opts.notes else os.path.join(out_dir, "RELEASE_NOTES.md")
    if not opts.notes:
        text = pipeline.release_notes(repo, title, record, commit, artifacts, downloads,
                                      builder.note_lines(record), features, config.get("notes"))
        if not pipeline.write_generated_notes(notes, text):
            logger.info(_("Keeping the edited release notes in {0}; delete the file to regenerate them.").format(notes))
    logger.info(_("Staged in {0}; release notes: {1}").format(out_dir, notes))

    release = ReleasePlan(repo=repo, tag=tag, commit=commit, remote=opts.remote, title=title,
                          assets=downloads + sums,
                          notes=os.path.relpath(notes, repo).replace(os.sep, "/"),
                          publish=opts.publish, retag=opts.retag, remote_tag=remote_tag,
                          platforms=platforms, force=opts.force, merge_notes=not opts.notes)
    if opts.no_upload:
        if opts.retag:
            print("\n" + _("To publish, from {0}, tag {1} as {2} (git tag -f, and git push --force for the "
                           "tag), push the branch with that commit and the tag, then:").format(repo, commit[:10], tag))
        else:
            print("\n" + _("To publish, from {0}, tag {1} as {2}, push the branch with that commit and the tag, "
                           "then:").format(repo, commit[:10], tag))
        for destination in destinations:
            for line in destination.dry_run_lines(release):
                print(line)
        return

    steps, reads_notes, irreversible = pipeline.publish_steps(release, destinations)
    if not steps:
        logger.info(_("{0} is already published with these assets.").format(title))
        return
    confirm = not opts.yes and sys.stdin.isatty()
    if confirm and not opts.notes and reads_notes:
        for path in dict.fromkeys(step[step.index("--notes-file") + 1] for step in steps if "--notes-file" in step):
            pipeline.approve_notes(os.path.join(repo, path))
    print("\n" + _("In {0}:").format(repo))
    for step in steps:
        print("  " + command_line(step))
    if not opts.yes:
        if not confirm:
            sys.exit(_("Not run: pass --yes to upload without a terminal to confirm in."))
        if not _confirm(_("Run these?")):
            sys.exit(_("Not run."))
    if irreversible and not opts.yes_irreversible:
        if not sys.stdin.isatty():
            sys.exit(_("Not run: steps for {0} cannot be undone; pass --yes-irreversible to run them "
                       "without a terminal.").format(", ".join(irreversible)))
        if not _confirm(_("Steps for {0} cannot be undone. Go ahead?").format(", ".join(irreversible))):
            sys.exit(_("Not run."))
    for step in steps:
        with timed(f"> {command_line(step)}"):
            if run_logged(step, cwd=repo) != 0:
                sys.exit(_("Stopped: the command above failed. Running this again resumes from there."))
    if opts.publish:
        logger.info(_("Published {0}.").format(title))
    elif any(d.name == "github" for d in destinations):
        logger.info(_("{0} is a draft release; publish it on GitHub, or run this again with --publish.").format(title))


def renamed(argv=None) -> None:
    """The ``nuitka-release`` entry point."""
    sys.exit(_("nuitka-release is now app-release: run app-release with the same arguments "
               "(app-release --memory-wait-plugin for the plugin's path)."))


def run(argv=None) -> None:
    """The console entry point: main() with each outcome logged and timed."""
    started = time.monotonic()
    try:
        try:
            main(argv)
        except SystemExit as e:
            if e.code not in (None, 0):
                if isinstance(e.code, str):
                    logger.error(e.code)
                logger.error(_("Stopped after {0}").format(duration(time.monotonic() - started)))
                sys.exit(1)
            raise
        except KeyboardInterrupt:
            logger.error(_("Interrupted after {0}").format(duration(time.monotonic() - started)))
            sys.exit(1)
        except BaseException:
            logger.exception(_("Failed after {0}").format(duration(time.monotonic() - started)))
            sys.exit(1)
        logger.info(_("Done in {0}").format(duration(time.monotonic() - started)))
    finally:
        log.finish()
