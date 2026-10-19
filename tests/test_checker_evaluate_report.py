from __future__ import annotations

import pytest

from standards_checker import report
from standards_checker.chunker import Chunk
from standards_checker.client import DecisionApiError
from standards_checker.config import parse_config
from standards_checker.evaluate import evaluate_answer, run_checks

CONFIG = parse_config(
    {
        "settings": {"max_questions_per_request": 2, "context": "Test project"},
        "rules": [
            {"id": "no-secrets", "severity": "error", "threshold": 0.5, "instructions": "Hardcoded secret?"},
            {"id": "no-print", "severity": "warning", "instructions": "Uses print()?"},
            {"id": "routes-only", "severity": "warning", "paths": ["app/routes/**"], "instructions": "Route issue?"},
            {
                "id": "readability",
                "type": "score",
                "severity": "warning",
                "instructions": "How readable?",
                "criteria": ["bad", "ok", "good"],
                "min_score": 1.0,
            },
        ],
    }
)
RULES = {rule.id: rule for rule in CONFIG.rules}
CHUNK = Chunk("app/routes/items.py", "function create_item", ((10, "def create_item():"), (11, "    print(1)")))
OTHER = Chunk("app/db.py", "function connect", ((3, "def connect():"), (4, "    return 1")))


class ScriptedClient:
    """Returns answers from a per-chunk script keyed by file path."""

    def __init__(self, values: dict[str, dict[str, float]], fail_paths: set[str] | None = None) -> None:
        self.values = values
        self.fail_paths = fail_paths or set()
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        self.batches: list[tuple[str, list[str]]] = []

    def ask(self, state: str, questions: dict) -> dict:
        path = state.splitlines()[1].removeprefix("File: ")
        self.usage["requests"] += 1
        self.batches.append((path, sorted(questions)))
        if path in self.fail_paths:
            raise DecisionApiError("HTTP 500")
        out = {}
        for key, question in questions.items():
            kind = question["type"]
            value = self.values.get(path, {}).get(key, 0.0 if kind == "noul" else 2.0)
            out[key] = {"type": kind, kind: value}
        return out


def test_evaluate_noul_threshold_is_inclusive() -> None:
    rule = RULES["no-secrets"]
    assert evaluate_answer(rule, CHUNK, {"noul": 0.5}).violated
    assert not evaluate_answer(rule, CHUNK, {"noul": 0.49}).violated


def test_evaluate_score_below_minimum() -> None:
    rule = RULES["readability"]
    assert evaluate_answer(rule, CHUNK, {"score": 0.99}).violated
    assert not evaluate_answer(rule, CHUNK, {"score": 1.0}).violated
    assert evaluate_answer(rule, CHUNK, {"score": 0.5}).signal == "score=0.50 < 1.00"


def test_evaluate_rejects_bad_answer() -> None:
    with pytest.raises(DecisionApiError, match="unexpected answer for rule 'no-print'"):
        evaluate_answer(RULES["no-print"], CHUNK, {"score": 1})


def test_run_checks_batches_and_scopes_rules() -> None:
    client = ScriptedClient({"app/routes/items.py": {"no_secrets": 0.9, "no_print": 0.95, "readability": 0.4}})
    run = run_checks(CONFIG, ["app/routes/items.py", "app/db.py"], [CHUNK, OTHER], client)

    assert client.usage["requests"] == 4  # 4 rules -> 2 batches for the route file, 3 rules -> 2 batches for db
    assert sorted(len(keys) for path, keys in client.batches if path == "app/db.py") == [1, 2]
    assert all("routes_only" not in keys for path, keys in client.batches if path == "app/db.py")
    assert run.error_count == 1
    assert run.warning_count == 2
    assert [(r.rule.id, r.chunk.path) for r in run.violations] == [
        ("no-secrets", "app/routes/items.py"),
        ("no-print", "app/routes/items.py"),
        ("readability", "app/routes/items.py"),
    ]
    assert report.status(run) == "failed"


def test_run_checks_records_request_failures() -> None:
    client = ScriptedClient({}, fail_paths={"app/db.py"})
    run = run_checks(CONFIG, ["app/routes/items.py", "app/db.py"], [CHUNK, OTHER], client)
    assert run.error_count == 0
    assert run.errors and all(e.startswith("app/db.py:3 (function connect): HTTP 500") for e in run.errors)
    assert report.status(run) == "incomplete"


def test_status_values() -> None:
    clean = run_checks(CONFIG, ["app/db.py"], [OTHER], ScriptedClient({}))
    assert report.status(clean) == "passed"
    warn = run_checks(CONFIG, ["app/db.py"], [OTHER], ScriptedClient({"app/db.py": {"no_print": 0.7}}))
    assert report.status(warn) == "passed with warnings"


def test_annotations_escape_special_characters() -> None:
    chunk = Chunk("app/routes/a,b:c.py", "function f", ((1, "def f():"), (2, "    pass")))
    run = run_checks(CONFIG, [chunk.path], [chunk], ScriptedClient({chunk.path: {"no_secrets": 1.0}}))
    run.errors.append("app/x.py:1 (function g): 100% failed\nretry")
    lines = report.annotation_lines(run)
    assert lines[0].startswith("::error file=app/routes/a%2Cb%3Ac.py,line=1,endLine=2,title=no-secrets (error)::")
    assert lines[-1] == "::error title=Decision-1 request failed::app/x.py:1 (function g): 100%25 failed%0Aretry"


def test_markdown_report_contents() -> None:
    client = ScriptedClient({"app/routes/items.py": {"no_secrets": 0.9}})
    run = run_checks(CONFIG, ["app/routes/items.py"], [CHUNK], client)
    md = report.markdown(
        run, link_base="https://github.com/o/r/blob/abc", run_url="https://github.com/o/r/actions/runs/1"
    )

    assert md.startswith(report.MARKER)
    assert "**Result: failed**: 1 error(s), 0 warning(s)." in md
    assert (
        "| error | `no-secrets` | [`app/routes/items.py#L10-L11`](https://github.com/o/r/blob/abc/app/routes/items.py#L10-L11)"
        in md
    )
    assert "| `routes-only` | noul | warning | p >= 0.60 fails | 1 | 0 | p=0.00 |" in md
    assert "| `readability` | score | warning | score < 1.00 fails | 1 | 0 | score=2.00 |" in md
    assert "[Workflow run](https://github.com/o/r/actions/runs/1)" in md


def test_markdown_without_chunks() -> None:
    run = run_checks(CONFIG, [], [], ScriptedClient({}))
    md = report.markdown(run)
    assert "No Python code units were in scope" in md
    assert "| `no-secrets` | noul | error | p >= 0.50 fails | 0 | 0 | n/a |" in md


def test_json_report() -> None:
    run = run_checks(
        CONFIG, ["app/routes/items.py"], [CHUNK], ScriptedClient({"app/routes/items.py": {"no_print": 0.8}})
    )
    data = report.to_json(run)
    assert data["status"] == "passed with warnings"
    assert data["warnings"] == 1
    assert data["violations"] == [
        {
            "rule": "no-print",
            "severity": "warning",
            "type": "noul",
            "path": "app/routes/items.py",
            "start_line": 10,
            "end_line": 11,
            "symbol": "function create_item",
            "value": 0.8,
            "violated": True,
        }
    ]
    assert len(data["results"]) == 4
