from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.dashboard import build_html, compute, load_contract, load_records

NOW = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)


def _write(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def test_compute_matches_contract_aggregations(tmp_path: Path) -> None:
    log = tmp_path / "logs.jsonl"
    ok = {"event": "response_sent", "latency_ms": 100, "ttft_ms": 50, "cost_usd": 0.01,
          "tokens_in": 10, "tokens_out": 20, "quality_score": 0.8, "tool_success": True}
    _write(log, [
        {"event": "request_received", "ts": _ts(5)},
        {**ok, "ts": _ts(5)},
        {"event": "request_received", "ts": _ts(4)},
        {**ok, "latency_ms": 3000, "ts": _ts(4)},
        {"event": "request_received", "ts": _ts(3)},
        {"event": "request_failed", "error_type": "RuntimeError", "tool_success": False, "ts": _ts(3)},
        {"event": "request_received", "ts": _ts(120)},  # ngoài cửa sổ 60 phút
    ])
    ctx = compute(load_records(log), NOW, 60)
    s = ctx["summary"]
    assert s["count"] == 3
    assert round(s["error_rate_pct"], 1) == 33.3
    assert s["count_by_value"] == {"RuntimeError": 1}
    assert round(s["tool_success_rate_pct"], 1) == 66.7
    assert s["p99"] == 3000
    assert s["total_cost"] == 0.02
    assert (s["tokens_in"], s["tokens_out"]) == (20, 40)


def test_dashboard_renders_all_contract_panels(tmp_path: Path) -> None:
    log = tmp_path / "logs.jsonl"
    _write(log, [{"event": "request_received", "ts": _ts(1)}])
    page = build_html(NOW, log_path=log)
    for panel in load_contract()["panels"]:
        assert panel["title"] in page
        assert panel["unit"] in page
    assert "threshold" in page and "60 phút" in page
