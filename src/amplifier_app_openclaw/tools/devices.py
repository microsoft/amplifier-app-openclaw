"""OpenClaw devices tool — camera, screen, and location on paired nodes."""

from __future__ import annotations

from typing import Any

from amplifier_app_openclaw.tools.base import OpenClawToolBase


class OpenClawDevicesTool(OpenClawToolBase):
    """Access camera, screen, and location on paired OpenClaw nodes/devices."""

    @property
    def name(self) -> str:
        return "openclaw_devices"

    @property
    def description(self) -> str:
        return (
            "Interact with paired devices (phones, computers) through OpenClaw. "
            "Supports camera snapshots, screen recording, location, and running commands."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status", "camera_snap", "camera_list",
                        "screen_record", "location_get", "run", "notify",
                    ],
                    "description": "Device action to perform.",
                },
                "node": {
                    "type": "string",
                    "description": "Target node id or name.",
                },
                "facing": {
                    "type": "string",
                    "enum": ["front", "back", "both"],
                    "description": "Camera facing direction.",
                },
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command to run on the node.",
                },
                "title": {
                    "type": "string",
                    "description": "Notification title.",
                },
                "body": {
                    "type": "string",
                    "description": "Notification body text.",
                },
            },
            "required": ["action"],
        }
