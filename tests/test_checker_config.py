from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from standards_checker.config import ConfigError, glob_match, load_config, parse_config

REPO_CONFIG = Path(__file__).resolve().parents[1] / ".github" / "code-standards.yml"


def _config(*rules: dict, **settings) -> dict:
    return {"settings": settings, "rules": list(rules)}


NOUL = {"id": "no-print", "type": "noul", "instructions": "Does this code call print()?"}
SCORE = {
    "id": "readability",
    "type": "score",
    "instructions": "How readable is it?",
    "criteria": ["poor", "ok", "good"],
    "min_score": 1.0,
}


def test_repo_config_is_valid() -> None:
    config = load_config(REPO_CONFIG)
    ids = [rule.id for rule in config.rules]
    assert "no-hardcoded-secrets" in ids
    assert "type-hints" in ids
    assert "type-hints" not in [rule.id for rule in config.active_rules]
    assert {rule.severity for rule in config.active_rules} == {"error", "warning"}


def test_yaml_boolean_criteria_keys_are_normalized() -> None:
    raw = yaml.safe_load(
        """
rules:
  - id: no-print
    instructions: Does this code call print()?
    criteria:
      true: print is used
      false: print is not used
"""
    )
    rule = parse_config(raw).rules[0]
    assert rule.criteria == {"true": "print is used", "false": "print is not used"}
    assert rule.to_question() == {
        "type": "noul",
        "instructions": "Does this code call print()?",
        "criteria": {"true": "print is used", "false": "print is not used"},
    }


def test_defaults_and_question_key() -> None:
    config = parse_config(_config(NOUL, default_threshold=0.8))
    rule = config.rules[0]
    assert rule.severity == "warning"
    assert rule.threshold == 0.8
    assert rule.question_key == "no_print"
    assert config.settings.deployment == "decision-1"


def test_score_rule_question_uses_list_criteria() -> None:
    rule = parse_config(_config(SCORE)).rules[0]
    assert rule.min_score == 1.0
    assert rule.to_question()["criteria"] == ["poor", "ok", "good"]


def test_severity_off_disables_rule() -> None:
    config = parse_config(_config({**NOUL, "severity": "off"}, {**SCORE, "id": "other"}))
    assert [rule.id for rule in config.active_rules] == ["other"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "configuration must be a mapping"),
        ({"rules": []}, "rules must be a non-empty list"),
        (_config({**NOUL, "id": "Bad Id"}), "id must match"),
        (_config({**NOUL, "type": "yesno"}), "type must be one of"),
        (_config({**NOUL, "severity": "fatal"}), "severity must be one of"),
        (_config({**NOUL, "instructions": " "}), "instructions is required"),
        (_config({**NOUL, "threshold": 1.5}), "threshold must be a number between 0 and 1"),
        (_config({**NOUL, "threshold": True}), "threshold must be a number between 0 and 1"),
        (_config({**NOUL, "criteria": {"maybe": "x"}}), "noul criteria must be a mapping"),
        (_config({**NOUL, "min_score": 1}), "min_score only applies to score rules"),
        (_config({**SCORE, "criteria": ["only-one"]}), "score criteria must be a list"),
        (_config({**SCORE, "min_score": 5}), "min_score must be a number between 0 and 2"),
        (_config({**SCORE, "threshold": 0.5}), "threshold only applies to noul rules"),
        (_config({**NOUL, "enabled": "yes"}), "enabled must be true or false"),
        (_config({**NOUL, "colour": "red"}), "unknown keys colour"),
        (_config(NOUL, {**NOUL, "id": "no_print"}), "duplicate rule ids: no_print"),
        (_config(NOUL, scope="everything"), "settings.scope must be one of"),
        (_config(NOUL, concurrency=0), "settings.concurrency must be a positive integer"),
        (_config(NOUL, colour="red"), "unknown settings: colour"),
        (_config(NOUL, include=[1]), "settings.include must be a string or a list of strings"),
    ],
)
def test_invalid_config(data: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(data)


def test_load_config_reports_bad_yaml(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_load_config_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(tmp_path / "missing.yml")


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("bookstore/routes/books.py", "bookstore/**/*.py", True),
        ("bookstore/app.py", "bookstore/**/*.py", True),
        ("./bookstore/app.py", "bookstore/**/*.py", True),
        ("bookstore/routes/books.py", "bookstore/*.py", False),
        ("tests/test_books.py", "tests/**", True),
        ("docs/readme.md", "**/*.py", False),
        ("setup.py", "**/*.py", True),
        ("bookstore\\routes\\books.py", "bookstore/routes/**", True),
        ("a/b.py", "a/?.py", True),
    ],
)
def test_glob_match(path: str, pattern: str, expected: bool) -> None:
    assert glob_match(path, pattern) is expected


def test_rule_paths_and_settings_include_exclude() -> None:
    config = parse_config(_config({**NOUL, "paths": "bookstore/routes/**"}, include=["**/*.py"], exclude=["tests/**"]))
    rule = config.rules[0]
    assert rule.applies_to("bookstore/routes/books.py")
    assert not rule.applies_to("bookstore/db.py")
    assert config.settings.wants("bookstore/db.py")
    assert not config.settings.wants("tests/test_books.py")
    assert not config.settings.wants("README.md")
