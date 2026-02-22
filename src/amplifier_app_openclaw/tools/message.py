"""OpenClaw message tool — send messages through OpenClaw channels."""

from __future__ import annotations

from typing import Any

from amplifier_app_openclaw.tools.base import OpenClawToolBase


class OpenClawMessageTool(OpenClawToolBase):
    """Send messages via OpenClaw channel plugins (Discord, Slack, etc.)."""

    @property
    def name(self) -> str:
        return "openclaw_message"

    @property
    def description(self) -> str:
        return (
            "Send a message through OpenClaw messaging channels. "
            "Supports actions like send, react, and poll across configured channel plugins."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["send"],
                    "description": "Message action to perform.",
                },
                "target": {
                    "type": "string",
                    "description": "Target channel or user id/name.",
                },
                "message": {
                    "type": "string",
                    "description": "Message text to send.",
                },
                "channel": {
                    "type": "string",
                    "description": "Channel id for the message.",
                },
                "replyTo": {
                    "type": "string",
                    "description": "Message id to reply to.",
                },
            },
            "required": ["action", "message"],
        }
