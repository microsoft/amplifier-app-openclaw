"""Cost tracking – log entries and generate reports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

COST_LOG_PATH = Path.home() / ".openclaw" / "amplifier" / "cost_log.jsonl"


@dataclass
class CostEntry:
    timestamp: str
    session_id: str
    bundle: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    duration_seconds: float
    task_summary: str


def log_cost_entry(entry: CostEntry) -> None:
    """Append a cost entry to the JSONL log."""
    COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with COST_LOG_PATH.open("a") as f:
        f.write(json.dumps(asdict(entry)) + "\n")


def generate_cost_report(
    period: str = "day",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Read the JSONL log, filter, and return a summary report."""
    now = datetime.now(timezone.utc)
    cutoff_map = {
        "day": now - timedelta(days=1),
        "week": now - timedelta(weeks=1),
        "month": now - timedelta(days=30),
        "all": datetime.min.replace(tzinfo=timezone.utc),
    }
    cutoff = cutoff_map.get(period, cutoff_map["day"])

    entries: list[dict[str, Any]] = []
    if COST_LOG_PATH.exists():
        for line in COST_LOG_PATH.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Filter by time
            try:
                ts = datetime.fromisoformat(e["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            # Filter by session
            if session_id and e.get("session_id") != session_id:
                continue
            entries.append(e)

    total_cost = sum(e.get("estimated_cost", 0) for e in entries)
    total_input = sum(e.get("input_tokens", 0) for e in entries)
    total_output = sum(e.get("output_tokens", 0) for e in entries)

    # Breakdown by bundle
    by_bundle: dict[str, dict[str, Any]] = {}
    for e in entries:
        b = e.get("bundle", "unknown")
        if b not in by_bundle:
            by_bundle[b] = {"cost": 0.0, "tokens": 0, "count": 0}
        by_bundle[b]["cost"] += e.get("estimated_cost", 0)
        by_bundle[b]["tokens"] += e.get("input_tokens", 0) + e.get("output_tokens", 0)
        by_bundle[b]["count"] += 1

    # Top 5 tasks by cost
    top5 = sorted(entries, key=lambda x: x.get("estimated_cost", 0), reverse=True)[:5]
    top5_tasks = [
        {
            "task_summary": e.get("task_summary", ""),
            "estimated_cost": e.get("estimated_cost", 0),
            "tokens": e.get("input_tokens", 0) + e.get("output_tokens", 0),
        }
        for e in top5
    ]

    return {
        "period": period,
        "session_id": session_id,
        "total_cost": total_cost,
        "total_tokens": total_input + total_output,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "task_count": len(entries),
        "breakdown_by_bundle": by_bundle,
        "top_5_tasks": top5_tasks,
    }
