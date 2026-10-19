from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from standards_checker import cli, gitdiff

CONFIG = textwrap.dedent(
    """\
    settings:
      include: ["app/**/*.py"]
      scope: changed
      changed_lines_only: true
    rules:
      - id: no-secrets
        severity: error
        instructions: Hardcoded secret?
      - id: no-print
        severity: warning
        instructions: Uses print()?
    """
)

ORIGINAL = textwrap.dedent(
    '''\
    """App module."""


    def keep():
        """Unchanged."""
        return 1


    def edit_me():
        """Will change."""
        return 2
    '''
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "standards.yml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "mod.py").write_text(ORIGINAL, encoding="utf-8")
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (tmp_path / "app" / "mod.py").write_text(ORIGINAL.replace("return 2", "print(2)\n    return 2"), encoding="utf-8")
    (tmp_path / "app" / "new.py").write_text('def added():\n    """New."""\n    return 3\n', encoding="utf-8")
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "feature")
    return tmp_path


class FlagPrints:
    """Fake client: flags `no_print` when the code calls print()."""

    def __init__(self) -> None:
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        self.states: list[str] = []

    def ask(self, state: str, questions: dict) -> dict:
        self.usage["requests"] += 1
        self.states.append(state)
        flagged = "print(" in state
        return {key: {"type": "noul", "noul": 0.9 if flagged and key == "no_print" else 0.0} for key in questions}


class FlagSecrets(FlagPrints):
    def ask(self, state: str, questions: dict) -> dict:
        answers = super().ask(state, questions)
        answers["no_secrets"] = {"type": "noul", "noul": 0.99}
        return answers


def test_parse_changed_lines() -> None:
    diff = "@@ -3,0 +4,2 @@\n+a\n+b\n@@ -10 +12 @@\n-x\n+y\n@@ -20,3 +21,0 @@\n-z\n"
    assert gitdiff.parse_changed_lines(diff) == {4, 5, 12}


def test_gitdiff_helpers(repo: Path) -> None:
    assert sorted(gitdiff.changed_files("main", repo)) == ["README.md", "app/mod.py", "app/new.py"]
    assert gitdiff.changed_lines("main", "app/mod.py", repo) == {11}
    assert "app/mod.py" in gitdiff.all_files(repo)
    with pytest.raises(gitdiff.GitError):
        gitdiff.changed_files("does-not-exist", repo)


def _run(repo: Path, monkeypatch: pytest.MonkeyPatch, client_cls: type, *args: str) -> tuple[int, FlagPrints]:
    client = client_cls()
    monkeypatch.setattr(cli, "MockDecisionClient", lambda: client)
    for key in ("GITHUB_STEP_SUMMARY", "GITHUB_OUTPUT", "BASE_REF"):
        monkeypatch.delenv(key, raising=False)
    code = cli.main(["--config", str(repo / "standards.yml"), "--root", str(repo), "--mock", *args])
    return code, client


def test_changed_lines_mode_only_checks_touched_units(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    code, client = _run(repo, monkeypatch, FlagPrints, "--base-ref", "main")
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    checked = sorted(state.splitlines()[0] + " " + state.splitlines()[1].split(" (")[0] for state in client.states)
    assert checked == ["File: app/mod.py Code unit: function edit_me", "File: app/new.py Code unit: function added"]
    assert "::warning file=app/mod.py,line=9,endLine=12,title=no-print (warning)::" in out
    assert "passed with warnings (0 errors, 1 warnings, 2 code units, 2 requests)" in out


def test_error_rules_fail_and_write_outputs(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    out_dir = tmp_path_factory.mktemp("out")
    gh_output = out_dir / "github_output"
    summary = out_dir / "summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))
    client = FlagSecrets()
    monkeypatch.setattr(cli, "MockDecisionClient", lambda: client)
    code = cli.main(
        [
            "--config",
            str(repo / "standards.yml"),
            "--root",
            str(repo),
            "--mock",
            "--scope",
            "all",
            "--summary-file",
            str(summary),
            "--markdown-out",
            str(out_dir / "report.md"),
            "--json-out",
            str(out_dir / "report.json"),
            "--no-annotations",
        ]
    )
    assert code == cli.EXIT_VIOLATIONS
    data = json.loads((out_dir / "report.json").read_text())
    assert data["status"] == "failed"
    assert data["files"] == ["app/mod.py", "app/new.py"]
    assert data["errors"] == 3  # keep, edit_me, added (a docstring-only module is not a code unit)
    assert gh_output.read_text() == "status=failed\nerrors=3\nwarnings=1\n"
    assert summary.read_text() == (out_dir / "report.md").read_text()


def test_explicit_files_respect_include(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, client = _run(repo, monkeypatch, FlagPrints, "--files", "app/new.py", "README.md", "app/missing.py")
    assert code == cli.EXIT_OK
    assert len(client.states) == 1 and "File: app/new.py" in client.states[0]


def test_changed_scope_without_base_ref_checks_everything(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    code, client = _run(repo, monkeypatch, FlagPrints)
    assert "::notice::No --base-ref given" in capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert client.usage["requests"] == 3


def test_invalid_config_exits_2(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text("rules: []\n", encoding="utf-8")
    assert cli.main(["--config", str(bad), "--mock"]) == cli.EXIT_FAILURE
    assert "::error title=Invalid standards config::" in capsys.readouterr().out


def test_bad_base_ref_exits_2(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    code, _ = _run(repo, monkeypatch, FlagPrints, "--base-ref", "nope")
    assert code == cli.EXIT_FAILURE
    assert "::error title=git failed::" in capsys.readouterr().out


def test_missing_endpoint_exits_2(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("DECISION_API_KEY", "x")
    code = cli.main(["--config", str(repo / "standards.yml"), "--root", str(repo), "--endpoint", "", "--scope", "all"])
    assert code == cli.EXIT_FAILURE
    assert "No endpoint configured" in capsys.readouterr().out
