"""Route context queries to appropriate agents/bundles based on query type."""

from __future__ import annotations

import re
import time
from collections import OrderedDict

# Query routing rules: (pattern, bundle, agent_hint)
QUERY_ROUTES = [
    # Architecture/design questions → superpowers if available, else foundation
    (r"(architect|design|pattern|structure|module)", "superpowers", "zen-architect"),
    # Code/implementation questions → python-dev or foundation
    (r"(code|implement|function|class|bug|error|fix)", "python-dev", None),
    # Research/exploration → foundation (general purpose)
    (r"(research|explore|find|search|compare)", "foundation", None),
    # Cost/usage → handled locally, no LLM needed
    (r"(cost|usage|token|spend|budget)", None, None),
]

DEFAULT_ROUTE = ("foundation", None)


def route_query(query: str) -> tuple[str | None, str | None]:
    """Determine the best bundle and agent for a query.

    Returns:
        (bundle_name, agent_hint) — bundle_name is None for locally-handled queries
    """
    query_lower = query.lower()
    for pattern, bundle, agent in QUERY_ROUTES:
        if re.search(pattern, query_lower):
            return (bundle, agent)
    return DEFAULT_ROUTE


class QueryCache:
    """Simple LRU cache for recent query results."""

    def __init__(self, max_size: int = 50, ttl_seconds: int = 300) -> None:
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._max_size = max_size
        self._ttl = ttl_seconds

    def get(self, query: str) -> dict | None:
        """Return cached result or None."""
        key = query.strip().lower()
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["time"] < self._ttl:
                self._cache.move_to_end(key)
                return entry["result"]
            del self._cache[key]
        return None

    def put(self, query: str, result: dict) -> None:
        """Cache a query result."""
        key = query.strip().lower()
        self._cache[key] = {"result": result, "time": time.time()}
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
