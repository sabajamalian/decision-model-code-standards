"""Command-line entry point: python -m standards_checker."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from standards_checker import gitdiff, report
from standards_checker.chunker import Chunk, chunk_python
from standards_checker.client import DecisionApiError, DecisionClient, MockDecisionClient, resolve_auth_headers
from standards_checker.config import Config, ConfigError, load_config
from standards_checker.evaluate import RunReport, run_checks

EXIT_OK = 0
EXIT_VIOLATIONS = 1
EXIT_FAILURE = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="standards_checker",
        description="Check Python code against configurable standards using Microsoft-Decision-1.",
    )
    parser.add_argument("--config", default=".github/code-standards.yml", help="Standards YAML file.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument(
        "--endpoint", default=os.environ.get("DECISION_ENDPOINT", ""), help="Foundry resource endpoint."
    )
    parser.add_argument("--deployment", default=os.environ.get("DECISION_DEPLOYMENT", ""), help="Deployment name.")
    parser.add_argument("--scope", choices=["changed", "all"], help="Override settings.scope.")
    parser.add_argument("--base-ref", default=os.environ.get("BASE_REF", ""), help="Diff base for scope=changed.")
    parser.add_argument("--files", nargs="*", help="Explicit files to check (ignores scope).")
    parser.add_argument("--mock", action="store_true", help="Use an offline client that reports no violations.")
    parser.add_argument(
        "--summary-file", default=os.environ.get("GITHUB_STEP_SUMMARY", ""), help="Append Markdown here."
    )
    parser.add_argument("--markdown-out", help="Write the Markdown report to this file.")
    parser.add_argument("--json-out", help="Write JSON results to this file.")
    parser.add_argument("--no-annotations", action="store_true", help="Do not print GitHub annotation commands.")
    return parser


def _select_files(args: argparse.Namespace, config: Config) -> tuple[list[str], dict[str, set[int]] | None]:
    """Return files to check and, for changed-lines mode, the changed lines per file."""
    settings = config.settings
    root = Path(args.root)
    if args.files:
        candidates = [Path(f).as_posix() for f in args.files]
        return [f for f in candidates if settings.wants(f) and (root / f).is_file()], None

    scope = args.scope or settings.scope
    if scope == "changed" and not args.base_ref:
        print("::notice::No --base-ref given; checking all files instead of changed files.")
        scope = "all"

    if scope == "all":
        return [f for f in gitdiff.all_files(root) if settings.wants(f) and (root / f).is_file()], None

    files = [f for f in gitdiff.changed_files(args.base_ref, root) if settings.wants(f) and (root / f).is_file()]
    if not settings.changed_lines_only:
        return files, None
    return files, {f: gitdiff.changed_lines(args.base_ref, f, root) for f in files}


def _collect_chunks(files: list[str], root: Path, max_lines: int, changed: dict[str, set[int]] | None) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in files:
        source = (root / path).read_text(encoding="utf-8", errors="replace")
        file_chunks = chunk_python(path, source, max_lines=max_lines)
        if changed is not None:
            file_chunks = [c for c in file_chunks if c.overlaps(changed.get(path, set()))]
        chunks.extend(file_chunks)
    return chunks


def _link_base() -> str | None:
    server, repo = os.environ.get("GITHUB_SERVER_URL"), os.environ.get("GITHUB_REPOSITORY")
    sha = os.environ.get("HEAD_SHA") or os.environ.get("GITHUB_SHA")
    return f"{server}/{repo}/blob/{sha}" if server and repo and sha else None


def _run_url() -> str | None:
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


def _write_outputs(run: RunReport, args: argparse.Namespace) -> None:
    md = report.markdown(run, link_base=_link_base(), run_url=_run_url())
    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as fh:
            fh.write(md)
    if args.markdown_out:
        Path(args.markdown_out).write_text(md, encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report.to_json(run), indent=2), encoding="utf-8")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write(f"status={report.status(run)}\nerrors={run.error_count}\nwarnings={run.warning_count}\n")


def main(argv: list[str] | None = None) -> int:
    """Run the check and return the process exit code (0 pass, 1 error violations, 2 failure)."""
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"::error title=Invalid standards config::{exc}")
        return EXIT_FAILURE
    if args.deployment:
        config = replace(config, settings=replace(config.settings, deployment=args.deployment))

    try:
        files, changed = _select_files(args, config)
    except gitdiff.GitError as exc:
        print(f"::error title=git failed::{exc}")
        return EXIT_FAILURE
    chunks = _collect_chunks(files, Path(args.root), config.settings.max_chunk_lines, changed)

    if args.mock:
        client = MockDecisionClient()
    else:
        try:
            client = DecisionClient(args.endpoint, config.settings.deployment, resolve_auth_headers())
        except DecisionApiError as exc:
            print(f"::error title=Decision-1 setup failed::{exc}")
            return EXIT_FAILURE

    run = run_checks(config, files, chunks, client) if chunks else RunReport(config, files, chunks, usage={})

    if not args.no_annotations:
        for line in report.annotation_lines(run):
            print(line)
    print(report.console(run))
    _write_outputs(run, args)

    if run.error_count:
        return EXIT_VIOLATIONS
    if run.errors:
        return EXIT_FAILURE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
