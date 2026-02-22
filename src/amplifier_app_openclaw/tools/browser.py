"""OpenClaw browser tool — web browsing and screenshots."""

from __future__ import annotations

from typing import Any

from amplifier_app_openclaw.tools.base import OpenClawToolBase


class OpenClawBrowserTool(OpenClawToolBase):
    """Control a web browser via OpenClaw for browsing, screenshots, and automation."""

    @property
    def name(self) -> str:
        return "openclaw_browser"

    @property
    def description(self) -> str:
        return (
            "Control a web browser through OpenClaw. Supports navigation, "
            "taking screenshots, capturing snapshots of page content, and UI automation."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "open", "navigate", "snapshot", "screenshot",
                        "act", "tabs", "close",
                    ],
                    "description": "Browser action to perform.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to open or navigate to.",
                },
                "ref": {
                    "type": "string",
                    "description": "Element reference for actions.",
                },
                "request": {
                    "type": "object",
                    "description": "Action request details (kind, text, ref, etc.).",
                },
            },
            "required": ["action"],
        }
