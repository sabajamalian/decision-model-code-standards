"""Run the questions for every chunk and turn answers into pass/violation results."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Protocol

from standards_checker.chunker import Chunk
from standards_checker.client import DecisionApiError
from standards_checker.config import Config, Rule


class Asker(Protocol):
    """Anything with an `ask(state, questions)` method (real or mock client)."""

    usage: dict[str, int]

    def ask(self, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Return answers keyed like `questions`."""


@dataclass(frozen=True)
class Result:
    """Outcome of one rule on one chunk."""

    rule: Rule
    chunk: Chunk
    value: float
    violated: bool

    @property
    def signal(self) -> str:
        """Human-readable model signal compared with the configured limit."""
        if self.rule.type == "noul":
            op = ">=" if self.violated else "<"
            return f"p={self.value:.2f} {op} {self.rule.threshold:.2f}"
        op = "<" if self.violated else ">="
        return f"score={self.value:.2f} {op} {self.rule.min_score:.2f}"


@dataclass
class RunReport:
    """Everything produced by a run, used by the reporters."""

    config: Config
    files: list[str]
    chunks: list[Chunk]
    results: list[Result] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def violations(self) -> list[Result]:
        """Violations sorted by severity, file and line."""
        order = {"error": 0, "warning": 1}
        found = [r for r in self.results if r.violated]
        return sorted(found, key=lambda r: (order.get(r.rule.severity, 2), r.chunk.path, r.chunk.start_line, r.rule.id))

    @property
    def error_count(self) -> int:
        """Number of violations of `error` rules."""
        return sum(1 for r in self.results if r.violated and r.rule.severity == "error")

    @property
    def warning_count(self) -> int:
        """Number of violations of `warning` rules."""
        return sum(1 for r in self.results if r.violated and r.rule.severity == "warning")


def evaluate_answer(rule: Rule, chunk: Chunk, answer: dict[str, Any]) -> Result:
    """Compare a Decision-1 answer with the rule's threshold (noul) or min_score (score)."""
    try:
        if rule.type == "noul":
            value = float(answer["noul"])
            return Result(rule, chunk, value, value >= rule.threshold)
        value = float(answer["score"])
        return Result(rule, chunk, value, value < (rule.min_score or 0.0))
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionApiError(f"unexpected answer for rule '{rule.id}': {answer!r}") from exc


def _batches(rules: Sequence[Rule], size: int) -> list[tuple[Rule, ...]]:
    return [tuple(rules[i : i + size]) for i in range(0, len(rules), size)]


def run_checks(config: Config, files: list[str], chunks: list[Chunk], client: Asker) -> RunReport:
    """Ask Decision-1 about each chunk (concurrently) and collect results and errors."""
    report = RunReport(config=config, files=files, chunks=chunks)
    settings = config.settings
    jobs: list[tuple[Chunk, tuple[Rule, ...]]] = []
    for chunk in chunks:
        rules = [rule for rule in config.active_rules if rule.applies_to(chunk.path)]
        jobs += [(chunk, batch) for batch in _batches(rules, settings.max_questions_per_request)]

    def run_job(chunk: Chunk, batch: tuple[Rule, ...]) -> list[Result]:
        questions = {rule.question_key: rule.to_question() for rule in batch}
        answers = client.ask(chunk.render(settings.context), questions)
        return [evaluate_answer(rule, chunk, answers[rule.question_key]) for rule in batch]

    with ThreadPoolExecutor(max_workers=settings.concurrency) as pool:
        futures = {pool.submit(run_job, chunk, batch): (chunk, batch) for chunk, batch in jobs}
        for future in as_completed(futures):
            chunk, _ = futures[future]
            try:
                report.results.extend(future.result())
            except DecisionApiError as exc:
                report.errors.append(f"{chunk.path}:{chunk.start_line} ({chunk.symbol}): {exc}")

    report.results.sort(key=lambda r: (r.chunk.path, r.chunk.start_line, r.rule.id))
    report.usage = dict(client.usage)
    return report
