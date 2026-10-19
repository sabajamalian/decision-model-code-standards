from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping

import pytest

from standards_checker.client import (
    API_PATH,
    DecisionApiError,
    DecisionClient,
    MockDecisionClient,
    resolve_auth_headers,
)

QUESTIONS = {
    "no_print": {"type": "noul", "instructions": "Does it print?"},
    "readability": {"type": "score", "instructions": "How readable?", "criteria": ["bad", "ok", "good"]},
}
ANSWERS = {
    "no_print": {"type": "noul", "noul": 0.97},
    "readability": {"type": "score", "score": 1.4, "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5}},
}


class FakeTransport:
    """Replays canned (status, headers, body) responses and records requests."""

    def __init__(self, *responses: tuple[int, Mapping[str, str], object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        self.calls.append((url, headers, json.loads(body)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, resp_headers, payload = response
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, resp_headers, data


def _ok(answers: dict = ANSWERS) -> tuple[int, dict, dict]:
    return 200, {}, {"answers": answers, "usage": {"input_tokens": 100, "output_tokens": 2}}


def _client(transport: FakeTransport, sleeps: list[float] | None = None, **kwargs) -> DecisionClient:
    return DecisionClient(
        "https://example.cognitiveservices.azure.com/",
        "decision-1",
        {"api-key": "test-key"},
        transport=transport,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        **kwargs,
    )


def test_ask_sends_expected_request_and_tracks_usage() -> None:
    transport = FakeTransport(_ok(), _ok())
    client = _client(transport)
    assert client.ask("code", QUESTIONS) == ANSWERS
    client.ask("code", QUESTIONS)

    url, headers, body = transport.calls[0]
    assert url == "https://example.cognitiveservices.azure.com" + API_PATH
    assert headers["api-key"] == "test-key"
    assert headers["Content-Type"] == "application/json"
    assert body == {"model": "decision-1", "state": "code", "questions": QUESTIONS}
    assert client.usage == {"requests": 2, "input_tokens": 200, "output_tokens": 4}


def test_retries_on_throttling_and_honours_retry_after() -> None:
    sleeps: list[float] = []
    transport = FakeTransport((429, {"Retry-After": "3"}, b"slow down"), (503, {}, b"busy"), _ok())
    client = _client(transport, sleeps)
    assert client.ask("code", QUESTIONS) == ANSWERS
    assert len(transport.calls) == 3
    assert sleeps[0] == 3.0
    assert 2.0 <= sleeps[1] <= 3.0


def test_retry_after_ms_header() -> None:
    sleeps: list[float] = []
    _client(FakeTransport((429, {"retry-after-ms": "1500"}, b""), _ok()), sleeps).ask("code", QUESTIONS)
    assert sleeps == [1.5]


def test_retries_network_errors() -> None:
    transport = FakeTransport(OSError("connection reset"), _ok())
    assert _client(transport).ask("code", QUESTIONS) == ANSWERS


def test_gives_up_after_max_retries() -> None:
    transport = FakeTransport(*[(429, {}, b"throttled")] * 3)
    with pytest.raises(DecisionApiError, match="HTTP 429"):
        _client(transport, max_retries=2).ask("code", QUESTIONS)
    assert len(transport.calls) == 3


def test_client_errors_are_not_retried() -> None:
    transport = FakeTransport((400, {}, b'{"error":"bad question"}'))
    with pytest.raises(DecisionApiError, match="HTTP 400.*bad question"):
        _client(transport).ask("code", QUESTIONS)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not json", "not valid JSON"),
        ({"result": {}}, "no 'answers' object"),
        ({"answers": {"no_print": ANSWERS["no_print"]}}, "missing answers for: readability"),
    ],
)
def test_malformed_responses(payload: object, message: str) -> None:
    with pytest.raises(DecisionApiError, match=message):
        _client(FakeTransport((200, {}, payload))).ask("code", QUESTIONS)


def test_endpoint_is_required() -> None:
    with pytest.raises(DecisionApiError, match="No endpoint configured"):
        DecisionClient("", "decision-1", {})


def test_endpoint_must_be_https() -> None:
    with pytest.raises(DecisionApiError, match="must start with https://"):
        DecisionClient("file:///etc/passwd", "decision-1", {})


def test_auth_prefers_api_key_then_token() -> None:
    assert resolve_auth_headers({"DECISION_API_KEY": "k", "DECISION_TOKEN": "t"}) == {"api-key": "k"}
    assert resolve_auth_headers({"DECISION_TOKEN": "t"}) == {"Authorization": "Bearer t"}


def test_auth_falls_back_to_azure_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("standards_checker.client.shutil.which", lambda _: "/usr/bin/az")
    seen: list[list[str]] = []

    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="cli-token\n", stderr="")

    headers = resolve_auth_headers({"DECISION_AZ_SUBSCRIPTION": "my-sub"}, run=run)
    assert headers == {"Authorization": "Bearer cli-token"}
    assert seen[0][:3] == ["/usr/bin/az", "account", "get-access-token"]
    assert seen[0][-2:] == ["--subscription", "my-sub"]


def test_auth_reports_azure_cli_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("standards_checker.client.shutil.which", lambda _: "/usr/bin/az")

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Please run 'az login'")

    with pytest.raises(DecisionApiError, match="az login"):
        resolve_auth_headers({}, run=run)


def test_auth_without_any_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("standards_checker.client.shutil.which", lambda _: None)
    with pytest.raises(DecisionApiError, match="No credentials found"):
        resolve_auth_headers({})


def test_mock_client_passes_everything() -> None:
    client = MockDecisionClient()
    answers = client.ask("code", QUESTIONS)
    assert answers["no_print"]["noul"] == 0.0
    assert answers["readability"]["score"] == 2.0
    assert client.usage["requests"] == 1
