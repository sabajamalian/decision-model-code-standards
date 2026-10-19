"""Minimal HTTP client for the Microsoft-Decision-1 `systemone` endpoint in Microsoft Foundry."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

API_PATH = "/providers/microsoft/v1/systemone"
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"  # noqa: S105 (Entra resource URI, not a secret)
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}

Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, Mapping[str, str], bytes]]


class DecisionApiError(RuntimeError):
    """Raised when the Decision-1 call fails or returns an unexpected payload."""


def urllib_transport(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, Mapping[str, str], bytes]:
    """POST `body` with urllib and return (status, headers, payload) without raising on HTTP errors."""
    # DecisionClient only builds https:// URLs, so file:/custom schemes cannot reach urlopen.
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


def resolve_auth_headers(
    env: Mapping[str, str] | None = None, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> dict[str, str]:
    """Pick credentials: DECISION_API_KEY, then DECISION_TOKEN, then `az account get-access-token`."""
    env = os.environ if env is None else env
    if env.get("DECISION_API_KEY"):
        return {"api-key": env["DECISION_API_KEY"]}
    if env.get("DECISION_TOKEN"):
        return {"Authorization": f"Bearer {env['DECISION_TOKEN']}"}
    az = shutil.which("az")
    if not az:
        raise DecisionApiError(
            "No credentials found. Set DECISION_API_KEY or DECISION_TOKEN, or sign in with the Azure CLI."
        )
    cmd = [az, "account", "get-access-token", "--resource", TOKEN_RESOURCE, "--query", "accessToken", "-o", "tsv"]
    if env.get("DECISION_AZ_SUBSCRIPTION"):
        cmd += ["--subscription", env["DECISION_AZ_SUBSCRIPTION"]]
    result = run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise DecisionApiError(f"az account get-access-token failed: {result.stderr.strip()}")
    return {"Authorization": f"Bearer {result.stdout.strip()}"}


class DecisionClient:
    """Sends `state` + `questions` to Decision-1 and returns the `answers` object."""

    def __init__(
        self,
        endpoint: str,
        deployment: str,
        auth_headers: dict[str, str],
        *,
        timeout: float = 60.0,
        max_retries: int = 5,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Configure the client; `endpoint` is the Foundry resource URL."""
        if not endpoint:
            raise DecisionApiError("No endpoint configured. Set DECISION_ENDPOINT or pass --endpoint.")
        if not endpoint.lower().startswith("https://"):
            raise DecisionApiError(f"Endpoint must start with https:// (got {endpoint!r}).")
        self.url = endpoint.rstrip("/") + API_PATH
        self.deployment = deployment
        self.auth_headers = auth_headers
        self.timeout = timeout
        self.max_retries = max_retries
        self.transport = transport
        self.sleep = sleep
        self._lock = threading.Lock()
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def _record_usage(self, usage: Any) -> None:
        with self._lock:
            self.usage["requests"] += 1
            if isinstance(usage, dict):
                self.usage["input_tokens"] += int(usage.get("input_tokens") or 0)
                self.usage["output_tokens"] += int(usage.get("output_tokens") or 0)

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        for key, value in headers.items():
            if key.lower() in ("retry-after", "retry-after-ms"):
                try:
                    seconds = float(value)
                except ValueError:
                    return None
                return seconds / 1000 if key.lower() == "retry-after-ms" else seconds
        return None

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, 60.0)
        return min(2.0**attempt + random.uniform(0, 1), 30.0)  # noqa: S311 (jitter, not crypto)

    def ask(self, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Ask every question about `state` in a single request and return the answers by key."""
        body = json.dumps({"model": self.deployment, "state": state, "questions": questions}).encode()
        headers = {"Content-Type": "application/json", **self.auth_headers}

        for attempt in range(self.max_retries + 1):
            try:
                status, resp_headers, payload = self.transport(self.url, headers, body, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == self.max_retries:
                    raise DecisionApiError(f"request to {self.url} failed: {exc}") from exc
                self.sleep(self._backoff(attempt, None))
                continue

            if status == 200:
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise DecisionApiError("response was not valid JSON") from exc
                answers = data.get("answers") if isinstance(data, dict) else None
                if not isinstance(answers, dict):
                    raise DecisionApiError("response has no 'answers' object")
                missing = sorted(set(questions) - set(answers))
                if missing:
                    raise DecisionApiError(f"response is missing answers for: {', '.join(missing)}")
                self._record_usage(data.get("usage"))
                return answers

            if status in RETRY_STATUSES and attempt < self.max_retries:
                self.sleep(self._backoff(attempt, self._retry_after(resp_headers)))
                continue
            detail = payload[:500].decode("utf-8", errors="replace")
            raise DecisionApiError(f"HTTP {status} from {self.url}: {detail}")

        raise DecisionApiError("retries exhausted")  # pragma: no cover


class MockDecisionClient:
    """Offline stand-in that reports every rule as passing. Useful for wiring tests and dry runs."""

    def __init__(self) -> None:
        """Start with zeroed usage counters."""
        self.usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}

    def ask(self, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Return a no-violation answer for each question."""
        self.usage["requests"] += 1
        answers: dict[str, dict[str, Any]] = {}
        for key, question in questions.items():
            if question["type"] == "score":
                answers[key] = {"type": "score", "score": float(len(question.get("criteria", [0, 0])) - 1)}
            else:
                answers[key] = {"type": "noul", "noul": 0.0}
        return answers
