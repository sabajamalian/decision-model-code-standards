"""Render results as GitHub annotations, Markdown (job summary / PR comment) and JSON."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from standards_checker.config import Rule
from standards_checker.evaluate import Result, RunReport

MARKER = "<!-- decision-standards-report -->"


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def rule_text(rule: Rule) -> str:
    """Short description of a rule for messages."""
    return rule.description or rule.instructions


def status(report: RunReport) -> str:
    """One of: failed, incomplete, passed with warnings, passed."""
    if report.error_count:
        return "failed"
    if report.errors:
        return "incomplete"
    if report.warning_count:
        return "passed with warnings"
    return "passed"


def annotation_lines(report: RunReport) -> list[str]:
    """GitHub workflow commands that create inline annotations for each violation."""
    lines = []
    for result in report.violations:
        level = "error" if result.rule.severity == "error" else "warning"
        title = f"{result.rule.id} ({result.rule.severity})"
        message = f"{rule_text(result.rule)} [{result.signal}] in {result.chunk.symbol}"
        lines.append(
            f"::{level} file={_escape_property(result.chunk.path)},line={result.chunk.start_line},"
            f"endLine={result.chunk.end_line},title={_escape_property(title)}::{_escape_data(message)}"
        )
    for error in report.errors:
        lines.append(f"::error title=Decision-1 request failed::{_escape_data(error)}")
    return lines


def _location(result: Result, link_base: str | None) -> str:
    chunk = result.chunk
    label = f"{chunk.path}#L{chunk.start_line}-L{chunk.end_line}"
    if link_base:
        return f"[`{label}`]({link_base}/{chunk.path}#L{chunk.start_line}-L{chunk.end_line})"
    return f"`{label}`"


def markdown(report: RunReport, *, link_base: str | None = None, run_url: str | None = None) -> str:
    """Markdown used for the job summary and the sticky PR comment."""
    settings = report.config.settings
    rules = report.config.active_rules
    usage = report.usage or {}
    out = [
        MARKER,
        "## Code standards check (Microsoft-Decision-1)",
        "",
        f"**Result: {status(report)}**: {report.error_count} error(s), {report.warning_count} warning(s).",
        "",
        f"Checked {len(report.chunks)} code unit(s) in {len(report.files)} file(s) against {len(rules)} rule(s) "
        f"using deployment `{settings.deployment}` "
        f"({usage.get('requests', 0)} request(s), {usage.get('input_tokens', 0):,} input tokens).",
        "",
    ]

    if report.violations:
        out += ["| Severity | Rule | Location | Code unit | Signal |", "|---|---|---|---|---|"]
        for result in report.violations:
            out.append(
                f"| {result.rule.severity} | `{result.rule.id}` | {_location(result, link_base)} | "
                f"{_cell(result.chunk.symbol)} | {result.signal} |"
            )
        out.append("")
    elif report.chunks:
        out += ["No violations found.", ""]
    else:
        out += ["No Python code units were in scope for this run.", ""]

    if report.errors:
        out += ["**Requests that failed** (these code units were not checked):", ""]
        out += [f"- {_cell(error)}" for error in report.errors[:20]]
        out.append("")

    by_rule: dict[str, list[Result]] = defaultdict(list)
    for result in report.results:
        by_rule[result.rule.id].append(result)
    out += [
        "<details><summary>Rules evaluated</summary>",
        "",
        "| Rule | Type | Severity | Limit | Units checked | Violations | Worst signal |",
        "|---|---|---|---|---|---|---|",
    ]
    for rule in rules:
        results = by_rule.get(rule.id, [])
        if rule.type == "noul":
            limit = f"p >= {rule.threshold:.2f} fails"
            worst = f"p={max(r.value for r in results):.2f}" if results else "n/a"
        else:
            limit = f"score < {rule.min_score:.2f} fails"
            worst = f"score={min(r.value for r in results):.2f}" if results else "n/a"
        violations = sum(1 for r in results if r.violated)
        out.append(
            f"| `{rule.id}` | {rule.type} | {rule.severity} | {limit} | {len(results)} | {violations} | {worst} |"
        )
    out += ["", "</details>", ""]

    footer = (
        "Decision-1 returns probabilities, not explanations, so each finding points at the code unit "
        "(function, class or module-level code) rather than an exact line. Adjust rules, thresholds and "
        "severities in `.github/code-standards.yml`."
    )
    if run_url:
        footer += f" [Workflow run]({run_url})."
    out.append(f"<sub>{footer}</sub>")
    return "\n".join(out) + "\n"


def to_json(report: RunReport) -> dict[str, Any]:
    """Machine-readable results."""

    def item(result: Result) -> dict[str, Any]:
        return {
            "rule": result.rule.id,
            "severity": result.rule.severity,
            "type": result.rule.type,
            "path": result.chunk.path,
            "start_line": result.chunk.start_line,
            "end_line": result.chunk.end_line,
            "symbol": result.chunk.symbol,
            "value": round(result.value, 4),
            "violated": result.violated,
        }

    return {
        "status": status(report),
        "errors": report.error_count,
        "warnings": report.warning_count,
        "files": report.files,
        "code_units": len(report.chunks),
        "usage": report.usage,
        "request_errors": report.errors,
        "violations": [item(r) for r in report.violations],
        "results": [item(r) for r in report.results],
    }


def console(report: RunReport) -> str:
    """Plain-text summary for logs and local runs."""
    lines = [
        f"Decision-1 standards check: {status(report)} "
        f"({report.error_count} errors, {report.warning_count} warnings, "
        f"{len(report.chunks)} code units, {report.usage.get('requests', 0)} requests)"
    ]
    for result in report.violations:
        lines.append(
            f"  {result.rule.severity.upper():7} {result.chunk.path}:{result.chunk.start_line}-{result.chunk.end_line} "
            f"{result.rule.id} [{result.signal}] {result.chunk.symbol}"
        )
    for error in report.errors:
        lines.append(f"  REQUEST-ERROR {error}")
    return "\n".join(lines)
