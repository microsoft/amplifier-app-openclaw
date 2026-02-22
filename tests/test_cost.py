"""Tests for the cost module."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from amplifier_app_openclaw.cost import CostEntry, generate_cost_report, log_cost_entry


@pytest.fixture
def cost_log(tmp_path, monkeypatch):
    log_path = tmp_path / "cost_log.jsonl"
    monkeypatch.setattr("amplifier_app_openclaw.cost.COST_LOG_PATH", log_path)
    return log_path


def _entry(hours_ago: float = 0, cost: float = 0.01, bundle: str = "foundation", session_id: str = "s1") -> CostEntry:
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return CostEntry(
        timestamp=ts, session_id=session_id, bundle=bundle,
        input_tokens=100, output_tokens=50, estimated_cost=cost,
        duration_seconds=1.5, task_summary="test task",
    )


class TestLogCostEntry:
    def test_append_creates_file(self, cost_log):
        log_cost_entry(_entry())
        assert cost_log.exists()
        lines = cost_log.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["bundle"] == "foundation"

    def test_append_multiple(self, cost_log):
        log_cost_entry(_entry())
        log_cost_entry(_entry(cost=0.02))
        lines = cost_log.read_text().strip().splitlines()
        assert len(lines) == 2


class TestGenerateCostReport:
    def test_empty_log(self, cost_log):
        report = generate_cost_report(period="all")
        assert report["task_count"] == 0
        assert report["total_cost"] == 0

    def test_period_day_filter(self, cost_log):
        log_cost_entry(_entry(hours_ago=2, cost=0.01))
        log_cost_entry(_entry(hours_ago=48, cost=0.99))  # outside day
        report = generate_cost_report(period="day")
        assert report["task_count"] == 1
        assert report["total_cost"] == pytest.approx(0.01)

    def test_period_all(self, cost_log):
        log_cost_entry(_entry(hours_ago=2, cost=0.01))
        log_cost_entry(_entry(hours_ago=48, cost=0.02))
        report = generate_cost_report(period="all")
        assert report["task_count"] == 2

    def test_session_filter(self, cost_log):
        log_cost_entry(_entry(session_id="s1"))
        log_cost_entry(_entry(session_id="s2"))
        report = generate_cost_report(period="all", session_id="s1")
        assert report["task_count"] == 1

    def test_breakdown_by_bundle(self, cost_log):
        log_cost_entry(_entry(bundle="a", cost=0.1))
        log_cost_entry(_entry(bundle="b", cost=0.2))
        report = generate_cost_report(period="all")
        assert "a" in report["breakdown_by_bundle"]
        assert "b" in report["breakdown_by_bundle"]
        assert report["breakdown_by_bundle"]["a"]["count"] == 1

    def test_top5(self, cost_log):
        for i in range(7):
            log_cost_entry(_entry(cost=i * 0.01))
        report = generate_cost_report(period="all")
        assert len(report["top_5_tasks"]) == 5

    def test_malformed_lines_skipped(self, cost_log):
        cost_log.write_text("not json\n")
        log_cost_entry(_entry())
        report = generate_cost_report(period="all")
        assert report["task_count"] == 1

    def test_report_structure(self, cost_log):
        report = generate_cost_report(period="all")
        expected_keys = {"period", "session_id", "total_cost", "total_tokens",
                         "total_input_tokens", "total_output_tokens", "task_count",
                         "breakdown_by_bundle", "top_5_tasks"}
        assert set(report.keys()) == expected_keys
