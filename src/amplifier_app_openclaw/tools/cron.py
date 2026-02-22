"""OpenClaw cron tool — schedule tasks."""

from __future__ import annotations

from typing import Any

from amplifier_app_openclaw.tools.base import OpenClawToolBase


class OpenClawCronTool(OpenClawToolBase):
    """Schedule and manage recurring or one-shot tasks through OpenClaw."""

    @property
    def name(self) -> str:
        return "openclaw_cron"

    @property
    def description(self) -> str:
        return (
            "Schedule tasks to run at specific times or intervals through OpenClaw. "
            "Supports cron expressions, one-shot delays, and task management."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "delete"],
                    "description": "Cron action to perform.",
                },
                "schedule": {
                    "type": "string",
                    "description": "Cron expression or delay (e.g. '*/5 * * * *' or '20m').",
                },
                "task": {
                    "type": "string",
                    "description": "Task description or prompt to execute.",
                },
                "channel": {
                    "type": "string",
                    "description": "Channel to deliver output to.",
                },
                "job_id": {
                    "type": "string",
                    "description": "Job id for deletion.",
                },
            },
            "required": ["action"],
        }
