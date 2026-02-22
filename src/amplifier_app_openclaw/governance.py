"""Standalone governance engine for tool-call evaluation.

Pattern-matching rule engine that evaluates tool calls against a set of rules
and returns an action (continue, deny, or ask_user). Not based on Amplifier's
hook system — this is a self-contained policy engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Rule:
    """A single governance rule."""

    pattern: str  # regex pattern matched against tool name + input text
    action: str  # "continue" | "deny" | "ask_user"
    reason: str = ""
    _compiled: re.Pattern | None = field(default=None, repr=False, compare=False)

    def compiled(self) -> re.Pattern:
        if self._compiled is None:
            self._compiled = re.compile(self.pattern)
        return self._compiled


# Default rules applied in order; first match wins.
DEFAULT_RULES: list[dict[str, str]] = [
    {"pattern": r"sudo\b", "action": "deny", "reason": "Root access not allowed"},
    {
        "pattern": r"(curl|wget)\s+.*\|\s*sh",
        "action": "deny",
        "reason": "Piped execution not allowed",
    },
    {"pattern": r"rm\s+.*-.*r.*f|rm\s+.*-.*f.*r|rm\s+-rf", "action": "ask_user", "reason": "Recursive deletion detected"},
    {"pattern": r"chmod\s+777", "action": "ask_user", "reason": "Broad permissions change detected"},
]


class GovernanceEngine:
    """Rule-based tool call governance engine.

    Evaluates tool invocations against an ordered list of pattern rules.
    First matching rule determines the action.  If no rule matches the
    default action is ``continue``.

    Parameters
    ----------
    rules:
        List of rule dicts with keys ``pattern``, ``action``, and optional
        ``reason``.  If *None*, :data:`DEFAULT_RULES` are used.
    """

    def __init__(self, rules: list[dict[str, str]] | None = None) -> None:
        raw = rules if rules is not None else DEFAULT_RULES
        self._rules: list[Rule] = [Rule(**r) for r in raw]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        tool: str,
        input_text: str | dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a tool call against the rule set.

        Returns a dict with keys:
        - ``action``: ``"continue"`` | ``"deny"`` | ``"ask_user"``
        - ``reason``: human-readable explanation (empty for continue)
        - ``prompt``: suggested prompt when action is ask_user
        - ``options``: suggested response options for ask_user
        """
        text = self._flatten(tool, input_text)

        for rule in self._rules:
            if rule.compiled().search(text):
                return self._result(rule.action, rule.reason)

        return self._result("continue", "")

    def add_rule(self, pattern: str, action: str, reason: str = "") -> None:
        """Append a rule to the engine."""
        self._rules.append(Rule(pattern=pattern, action=action, reason=reason))

    def load_rules(self, rules: list[dict[str, str]]) -> None:
        """Replace all rules with the given list."""
        self._rules = [Rule(**r) for r in rules]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten(tool: str, input_text: str | dict[str, Any]) -> str:
        if isinstance(input_text, dict):
            parts = [tool] + [str(v) for v in input_text.values()]
            return " ".join(parts)
        return f"{tool} {input_text}"

    @staticmethod
    def _result(action: str, reason: str) -> dict[str, Any]:
        result: dict[str, Any] = {"action": action, "reason": reason}
        if action == "ask_user":
            result["prompt"] = reason
            result["options"] = ["allow", "deny"]
        else:
            result["prompt"] = ""
            result["options"] = []
        return result
