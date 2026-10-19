"""Load and validate the standards configuration (YAML)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

RULE_TYPES = ("noul", "score")
SEVERITIES = ("error", "warning", "off")
SCOPES = ("changed", "all")
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ConfigError(ValueError):
    """Raised when the standards configuration is invalid."""


@dataclass(frozen=True)
class Rule:
    """One coding standard, asked as a single Decision-1 question per code chunk."""

    id: str
    type: str
    instructions: str
    severity: str = "warning"
    description: str = ""
    criteria: dict[str, str] | tuple[str, ...] | None = None
    threshold: float = 0.6
    min_score: float | None = None
    enabled: bool = True
    paths: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        """True if the rule should be evaluated."""
        return self.enabled and self.severity != "off"

    @property
    def question_key(self) -> str:
        """Key used for this rule in the Decision-1 `questions` object."""
        return self.id.replace("-", "_")

    def applies_to(self, path: str) -> bool:
        """True if the rule is scoped to this file (no `paths` means every file)."""
        return not self.paths or any(glob_match(path, pattern) for pattern in self.paths)

    def to_question(self) -> dict[str, Any]:
        """Build the Decision-1 question payload for this rule."""
        question: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.criteria:
            question["criteria"] = list(self.criteria) if isinstance(self.criteria, tuple) else dict(self.criteria)
        return question


@dataclass(frozen=True)
class Settings:
    """Global settings for a run."""

    deployment: str = "decision-1"
    include: tuple[str, ...] = ("**/*.py",)
    exclude: tuple[str, ...] = ()
    scope: str = "changed"
    changed_lines_only: bool = True
    context: str = ""
    default_threshold: float = 0.6
    max_chunk_lines: int = 150
    max_questions_per_request: int = 10
    concurrency: int = 4

    def wants(self, path: str) -> bool:
        """True if the file is in scope according to include/exclude globs."""
        if not any(glob_match(path, pattern) for pattern in self.include):
            return False
        return not any(glob_match(path, pattern) for pattern in self.exclude)


@dataclass(frozen=True)
class Config:
    """Parsed configuration: settings plus rules."""

    settings: Settings
    rules: tuple[Rule, ...] = field(default_factory=tuple)

    @property
    def active_rules(self) -> tuple[Rule, ...]:
        """Rules that are enabled and not set to severity `off`."""
        return tuple(rule for rule in self.rules if rule.active)


@lru_cache(maxsize=256)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, pattern: str) -> bool:
    """Match a POSIX-style relative path against a glob that supports `**`."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return bool(_glob_regex(pattern).match(normalized))


def _as_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ConfigError(f"{name} must be a string or a list of strings")


def _probability(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ConfigError(f"{name} must be a number between 0 and 1")
    return float(value)


def _parse_settings(raw: dict[str, Any]) -> Settings:
    known = set(Settings.__dataclass_fields__)
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")
    values: dict[str, Any] = dict(raw)
    for key in ("include", "exclude"):
        if key in values:
            values[key] = _as_tuple(values[key], f"settings.{key}")
    if values.get("scope", "changed") not in SCOPES:
        raise ConfigError(f"settings.scope must be one of {SCOPES}")
    if "default_threshold" in values:
        values["default_threshold"] = _probability(values["default_threshold"], "settings.default_threshold")
    for key in ("max_chunk_lines", "max_questions_per_request", "concurrency"):
        if key in values and (not isinstance(values[key], int) or values[key] < 1):
            raise ConfigError(f"settings.{key} must be a positive integer")
    return Settings(**values)


def _parse_rule(raw: Any, index: int, settings: Settings) -> Rule:
    if not isinstance(raw, dict):
        raise ConfigError(f"rules[{index}] must be a mapping")
    rule_id = raw.get("id")
    where = f"rule '{rule_id}'" if rule_id else f"rules[{index}]"
    if not isinstance(rule_id, str) or not _RULE_ID.match(rule_id):
        raise ConfigError(f"{where}: id must match {_RULE_ID.pattern}")

    rule_type = raw.get("type", "noul")
    if rule_type not in RULE_TYPES:
        raise ConfigError(f"{where}: type must be one of {RULE_TYPES}")
    severity = raw.get("severity", "warning")
    if severity not in SEVERITIES:
        raise ConfigError(f"{where}: severity must be one of {SEVERITIES}")
    instructions = raw.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ConfigError(f"{where}: instructions is required")

    criteria = raw.get("criteria")
    min_score = None
    if rule_type == "noul":
        if criteria is not None:
            # YAML parses bare `true:`/`false:` keys as booleans, so normalize them.
            if not isinstance(criteria, dict):
                raise ConfigError(f"{where}: noul criteria must be a mapping with 'true'/'false' keys")
            criteria = {str(k).lower(): str(v) for k, v in criteria.items()}
            if not criteria or set(criteria) - {"true", "false"}:
                raise ConfigError(f"{where}: noul criteria must be a mapping with 'true'/'false' keys")
        if "min_score" in raw:
            raise ConfigError(f"{where}: min_score only applies to score rules")
    else:
        if not isinstance(criteria, list) or len(criteria) < 2 or not all(isinstance(c, str) for c in criteria):
            raise ConfigError(f"{where}: score criteria must be a list of at least two labels, low to high")
        criteria = tuple(criteria)
        min_score = raw.get("min_score")
        if (
            isinstance(min_score, bool)
            or not isinstance(min_score, (int, float))
            or not 0 <= min_score <= len(criteria) - 1
        ):
            raise ConfigError(f"{where}: min_score must be a number between 0 and {len(criteria) - 1}")
        min_score = float(min_score)
        if "threshold" in raw:
            raise ConfigError(f"{where}: threshold only applies to noul rules")

    threshold = _probability(raw.get("threshold", settings.default_threshold), f"{where}: threshold")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{where}: enabled must be true or false")

    known = {
        "id",
        "type",
        "severity",
        "instructions",
        "description",
        "criteria",
        "threshold",
        "min_score",
        "enabled",
        "paths",
    }
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"{where}: unknown keys {', '.join(sorted(unknown))}")

    return Rule(
        id=rule_id,
        type=rule_type,
        instructions=instructions.strip(),
        severity=severity,
        description=str(raw.get("description") or "").strip(),
        criteria=criteria,
        threshold=threshold,
        min_score=min_score,
        enabled=enabled,
        paths=_as_tuple(raw.get("paths"), f"{where}: paths"),
    )


def parse_config(data: Any) -> Config:
    """Validate a raw mapping (already loaded from YAML) and return a Config."""
    if not isinstance(data, dict):
        raise ConfigError("configuration must be a mapping with 'settings' and 'rules'")
    settings_raw = data.get("settings") or {}
    if not isinstance(settings_raw, dict):
        raise ConfigError("settings must be a mapping")
    settings = _parse_settings(settings_raw)

    rules_raw = data.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ConfigError("rules must be a non-empty list")
    rules = tuple(_parse_rule(raw, i, settings) for i, raw in enumerate(rules_raw))

    keys = [rule.question_key for rule in rules]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ConfigError(f"duplicate rule ids: {', '.join(duplicates)}")
    return Config(settings=settings, rules=rules)


def load_config(path: str | Path) -> Config:
    """Read and validate a YAML configuration file."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    return parse_config(data)
