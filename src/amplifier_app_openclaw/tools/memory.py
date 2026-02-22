"""OpenClaw memory tool — read/write workspace files and memory."""

from __future__ import annotations

from typing import Any

from amplifier_app_openclaw.tools.base import OpenClawToolBase


class OpenClawMemoryTool(OpenClawToolBase):
    """Read and write workspace files and memory through OpenClaw."""

    @property
    def name(self) -> str:
        return "openclaw_memory"

    @property
    def description(self) -> str:
        return (
            "Read, write, and edit files in the OpenClaw workspace. "
            "Use for persistent memory, notes, and file management."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "edit"],
                    "description": "File operation to perform.",
                },
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write (for write action).",
                },
                "old_string": {
                    "type": "string",
                    "description": "Text to find (for edit action).",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text (for edit action).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line offset for reading (1-indexed).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to read.",
                },
            },
            "required": ["action", "path"],
        }
