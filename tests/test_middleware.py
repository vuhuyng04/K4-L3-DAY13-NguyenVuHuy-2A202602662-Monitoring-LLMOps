from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from app import logging_config
from app.main import app
from app.middleware import resolve_correlation_id

ID_FORMAT = re.compile(r"^req-[0-9a-f]{8}$")


def _post(headers: dict[str, str] | None = None, user_id: str = "student-01") -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/chat",
                headers=headers or {},
                json={
                    "user_id": user_id,
                    "session_id": "session-01",
                    "feature": "qa",
                    "message": "Explain monitoring",
                },
            )

    return asyncio.run(send())


def test_resolve_correlation_id_accepts_valid_and_replaces_invalid() -> None:
    assert resolve_correlation_id("req-abcdef12") == "req-abcdef12"
    assert ID_FORMAT.fullmatch(resolve_correlation_id(None))
    assert ID_FORMAT.fullmatch(resolve_correlation_id("evil\n{\"x\":1}"))
    assert resolve_correlation_id("evil\n") != "evil\n"


def test_incoming_request_id_is_propagated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logging_config, "LOG_PATH", tmp_path / "logs.jsonl")
    response = _post({"x-request-id": "req-0badcafe"})
    assert response.headers["x-request-id"] == "req-0badcafe"
    assert response.json()["correlation_id"] == "req-0badcafe"
    assert float(response.headers["x-response-time-ms"]) >= 0


def test_generated_ids_are_unique_and_logs_are_enriched(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    first = _post(user_id="student-a").headers["x-request-id"]
    second = _post(user_id="student-b").headers["x-request-id"]
    assert ID_FORMAT.fullmatch(first) and ID_FORMAT.fullmatch(second)
    assert first != second

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    api_events = [e for e in events if e.get("service") == "api"]
    assert {e["correlation_id"] for e in api_events} == {first, second}
    for event in api_events:
        for field in ("user_id_hash", "session_id", "feature", "model", "env"):
            assert field in event
    # Không rò context: mỗi request mang user_id_hash của chính nó.
    by_id = {e["correlation_id"]: e["user_id_hash"] for e in api_events}
    assert by_id[first] != by_id[second]


def test_failed_request_returns_and_logs_correlation_id(monkeypatch, tmp_path: Path) -> None:
    from app import incidents

    log_path = tmp_path / "logs.jsonl"
    monkeypatch.setattr(logging_config, "LOG_PATH", log_path)
    monkeypatch.setitem(incidents.STATE, "tool_fail", True)
    response = _post({"x-request-id": "req-00fa11ed"})

    assert response.status_code == 500
    assert response.json() == {"detail": "RuntimeError", "correlation_id": "req-00fa11ed"}
    assert response.headers["x-request-id"] == "req-00fa11ed"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    failed = next(e for e in events if e["event"] == "request_failed")
    assert failed["correlation_id"] == "req-00fa11ed"
    assert failed["tool_success"] is False
    assert failed["error_type"] == "RuntimeError"
