"""Tests for context_router: query routing and caching."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from amplifier_app_openclaw.context_router import QueryCache, route_query


# -- route_query tests --------------------------------------------------------

class TestRouteQuery:
    def test_architecture_routes_to_superpowers(self):
        bundle, agent = route_query("What is the design pattern here?")
        assert bundle == "superpowers"
        assert agent == "zen-architect"

    def test_code_routes_to_python_dev(self):
        bundle, agent = route_query("Fix this bug in my function")
        assert bundle == "python-dev"
        assert agent is None

    def test_research_routes_to_foundation(self):
        bundle, agent = route_query("Search for alternatives")
        assert bundle == "foundation"
        assert agent is None

    def test_cost_routes_locally(self):
        bundle, agent = route_query("Show me token usage")
        assert bundle is None
        assert agent is None

    def test_default_route(self):
        bundle, agent = route_query("Tell me a joke")
        assert bundle == "foundation"
        assert agent is None

    def test_case_insensitive(self):
        bundle, agent = route_query("ARCHITECT this system")
        assert bundle == "superpowers"

    def test_first_match_wins(self):
        # "design" matches before "code"
        bundle, _ = route_query("design the code structure")
        assert bundle == "superpowers"


# -- QueryCache tests ---------------------------------------------------------

class TestQueryCache:
    def test_put_and_get(self):
        cache = QueryCache()
        cache.put("hello", {"response": "world"})
        assert cache.get("hello") == {"response": "world"}

    def test_get_miss(self):
        cache = QueryCache()
        assert cache.get("nonexistent") is None

    def test_normalized_key(self):
        cache = QueryCache()
        cache.put("  Hello World  ", {"r": 1})
        assert cache.get("hello world") == {"r": 1}

    def test_ttl_expiry(self):
        cache = QueryCache(ttl_seconds=1)
        cache.put("q", {"r": 1})
        assert cache.get("q") is not None
        with patch.object(time, "time", return_value=time.time() + 2):
            assert cache.get("q") is None

    def test_max_size_eviction(self):
        cache = QueryCache(max_size=2)
        cache.put("a", {"r": 1})
        cache.put("b", {"r": 2})
        cache.put("c", {"r": 3})
        # "a" should be evicted
        assert cache.get("a") is None
        assert cache.get("b") == {"r": 2}
        assert cache.get("c") == {"r": 3}

    def test_lru_touch_on_get(self):
        cache = QueryCache(max_size=2)
        cache.put("a", {"r": 1})
        cache.put("b", {"r": 2})
        # Touch "a" so "b" becomes LRU
        cache.get("a")
        cache.put("c", {"r": 3})
        assert cache.get("a") == {"r": 1}
        assert cache.get("b") is None


# -- _find_session_by_bundle tests -------------------------------------------

class TestFindSessionByBundle:
    def test_finds_matching_ready_session(self):
        """Test via SessionManager._find_session_by_bundle."""
        from unittest.mock import MagicMock
        from amplifier_app_openclaw.session_manager import SessionManager, SessionState

        mgr = SessionManager.__new__(SessionManager)
        mgr._sessions = {
            "s1": MagicMock(metadata={"bundle": "foundation", "status": "ready"}),
            "s2": MagicMock(metadata={"bundle": "python-dev", "status": "executing"}),
        }
        assert mgr._find_session_by_bundle("foundation") == "s1"
        assert mgr._find_session_by_bundle("python-dev") is None  # not ready
        assert mgr._find_session_by_bundle("nonexistent") is None
