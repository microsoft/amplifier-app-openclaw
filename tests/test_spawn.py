"""Tests for CLISpawnManager and config merge utilities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_app_openclaw.spawn import (
    CLISpawnManager,
    _apply_provider_override,
    _deep_merge,
    _filter_hooks,
    _filter_tools,
    _merge_configs,
    _merge_module_lists,
)


# ---------------------------------------------------------------------------
# Config merge utility tests
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_simple_override(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested_merge(self):
        base = {"a": {"b": 1, "c": 2}}
        overlay = {"a": {"c": 3, "d": 4}}
        assert _deep_merge(base, overlay) == {"a": {"b": 1, "c": 3, "d": 4}}

    def test_disjoint_keys(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_array_replaced_not_merged(self):
        assert _deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}

    def test_empty_overlay(self):
        assert _deep_merge({"a": 1}, {}) == {"a": 1}

    def test_empty_base(self):
        assert _deep_merge({}, {"a": 1}) == {"a": 1}


class TestMergeModuleLists:
    def test_disjoint_modules(self):
        base = [{"module": "a", "config": {"x": 1}}]
        overlay = [{"module": "b", "config": {"y": 2}}]
        result = _merge_module_lists(base, overlay)
        modules = {item["module"] for item in result}
        assert modules == {"a", "b"}

    def test_overlapping_module_merges_config(self):
        base = [{"module": "a", "config": {"x": 1, "y": 2}}]
        overlay = [{"module": "a", "config": {"y": 3, "z": 4}}]
        result = _merge_module_lists(base, overlay)
        assert len(result) == 1
        assert result[0]["config"] == {"x": 1, "y": 3, "z": 4}

    def test_items_without_module_key_ignored(self):
        base = [{"module": "a"}, {"no_module": True}]
        overlay = [{"module": "b"}]
        result = _merge_module_lists(base, overlay)
        modules = [item["module"] for item in result]
        assert modules == ["a", "b"]


class TestFilterTools:
    def test_exclude_tools(self):
        config = {"tools": [{"module": "a"}, {"module": "b"}, {"module": "c"}]}
        result = _filter_tools(config, {"exclude_tools": ["b"]})
        assert [t["module"] for t in result["tools"]] == ["a", "c"]

    def test_inherit_tools_allowlist(self):
        config = {"tools": [{"module": "a"}, {"module": "b"}, {"module": "c"}]}
        result = _filter_tools(config, {"inherit_tools": ["a", "c"]})
        assert [t["module"] for t in result["tools"]] == ["a", "c"]

    def test_no_filtering_returns_same_object(self):
        config = {"tools": [{"module": "a"}]}
        result = _filter_tools(config, {})
        assert result is config

    def test_empty_tools_list(self):
        config = {"tools": []}
        result = _filter_tools(config, {"exclude_tools": ["a"]})
        assert result is config

    def test_preserves_other_keys(self):
        config = {"tools": [{"module": "a"}, {"module": "b"}], "providers": []}
        result = _filter_tools(config, {"exclude_tools": ["b"]})
        assert "providers" in result


class TestFilterHooks:
    def test_exclude_hooks(self):
        config = {"hooks": [{"module": "a"}, {"module": "b"}]}
        result = _filter_hooks(config, {"exclude_hooks": ["a"]})
        assert [h["module"] for h in result["hooks"]] == ["b"]

    def test_inherit_hooks_allowlist(self):
        config = {"hooks": [{"module": "a"}, {"module": "b"}, {"module": "c"}]}
        result = _filter_hooks(config, {"inherit_hooks": ["b"]})
        assert [h["module"] for h in result["hooks"]] == ["b"]

    def test_no_filtering_returns_same_object(self):
        config = {"hooks": [{"module": "a"}]}
        result = _filter_hooks(config, {})
        assert result is config


class TestApplyProviderOverride:
    def test_promote_provider_by_short_name(self):
        config = {
            "providers": [
                {"module": "provider-openai", "config": {"priority": 100}},
                {"module": "provider-anthropic", "config": {"priority": 100}},
            ]
        }
        result = _apply_provider_override(config, "anthropic", "claude-3")
        anthropic = [
            p for p in result["providers"] if "anthropic" in p["module"]
        ][0]
        assert anthropic["config"]["priority"] == 0
        assert anthropic["config"]["model"] == "claude-3"

    def test_model_only_targets_highest_priority(self):
        config = {
            "providers": [
                {"module": "provider-openai", "config": {"priority": 50}},
                {"module": "provider-anthropic", "config": {"priority": 100}},
            ]
        }
        result = _apply_provider_override(config, None, "gpt-4o")
        assert result["providers"][0]["config"]["model"] == "gpt-4o"
        assert result["providers"][0]["config"]["priority"] == 0

    def test_no_override_returns_same_object(self):
        config = {"providers": [{"module": "x"}]}
        assert _apply_provider_override(config, None, None) is config

    def test_empty_providers_returns_same_object(self):
        config = {"providers": []}
        assert _apply_provider_override(config, "x", "y") is config

    def test_unknown_provider_returns_unchanged(self):
        config = {"providers": [{"module": "provider-openai", "config": {}}]}
        result = _apply_provider_override(config, "nonexistent", "model")
        assert result is config


class TestMergeConfigs:
    def test_basic_merge(self):
        parent = {
            "tools": [{"module": "a"}],
            "providers": [{"module": "p1"}],
        }
        overlay = {"tools": [{"module": "b"}]}
        result = _merge_configs(parent, overlay)
        tool_modules = [t["module"] for t in result["tools"]]
        assert "a" in tool_modules
        assert "b" in tool_modules

    def test_spawn_exclude_tools_applied(self):
        parent = {
            "tools": [{"module": "a"}, {"module": "b"}],
            "spawn": {"exclude_tools": ["b"]},
        }
        result = _merge_configs(parent, {})
        assert [t["module"] for t in result["tools"]] == ["a"]

    def test_agent_filter_none_clears_agents(self):
        parent = {"agents": {"explorer": {}, "builder": {}}}
        overlay = {"agents": "none"}
        result = _merge_configs(parent, overlay)
        assert result["agents"] == {}

    def test_agent_filter_list_selects_subset(self):
        parent = {"agents": {"explorer": {"x": 1}, "builder": {"y": 2}}}
        overlay = {"agents": ["explorer"]}
        result = _merge_configs(parent, overlay)
        assert list(result["agents"].keys()) == ["explorer"]

    def test_agent_filter_all_inherits_parent(self):
        parent = {"agents": {"explorer": {}, "builder": {}}}
        overlay = {"agents": "all"}
        result = _merge_configs(parent, overlay)
        # "all" is not "none" and not a list, so agents pass through unchanged
        assert "explorer" in result["agents"]
        assert "builder" in result["agents"]


# ---------------------------------------------------------------------------
# Helpers for CLISpawnManager tests
# ---------------------------------------------------------------------------


def _make_child_session_mock():
    """Create a mock child AmplifierSession with async methods."""
    child = MagicMock()
    child.coordinator = MagicMock()
    child.coordinator.mount = AsyncMock()
    child.coordinator.register_capability = MagicMock()
    child.coordinator.cancellation = MagicMock()
    child.coordinator.cancellation.register_child = MagicMock()
    child.coordinator.cancellation.unregister_child = MagicMock()
    child.initialize = AsyncMock()
    child.execute = AsyncMock(return_value="Done exploring")
    child.cleanup = AsyncMock()
    return child


def _make_parent_session_mock():
    """Create a mock parent session with coordinator and config."""
    session = MagicMock()
    session.session_id = "parent-session-001"
    session.config = {
        "tools": [{"module": "tool-filesystem"}],
        "providers": [{"module": "provider-anthropic", "config": {}}],
        "agents": {
            "explorer": {"system": {"instruction": "You are an explorer"}},
        },
    }
    session.coordinator.get = MagicMock(return_value=None)
    session.coordinator.get_capability = MagicMock(return_value=None)
    session.coordinator.cancellation = MagicMock()
    session.coordinator.cancellation.register_child = MagicMock()
    session.coordinator.cancellation.unregister_child = MagicMock()
    return session


# ---------------------------------------------------------------------------
# CLISpawnManager tests
# ---------------------------------------------------------------------------


class TestCLISpawnManagerSpawn:
    @pytest.mark.asyncio
    async def test_spawn_happy_path(self):
        """Child session is created, initialized, executed, and cleaned up."""
        prepared = MagicMock()
        mgr = CLISpawnManager(prepared)

        parent = _make_parent_session_mock()
        child = _make_child_session_mock()
        child.execute.return_value = "Survey complete"

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ):
            result = await mgr.spawn(
                agent_name="explorer",
                instruction="Survey the codebase",
                parent_session=parent,
                agent_configs={
                    "explorer": {"system": {"instruction": "Explore"}},
                },
            )

        assert result["output"] == "Survey complete"
        assert "session_id" in result
        child.initialize.assert_awaited_once()
        child.execute.assert_awaited_once_with("Survey the codebase")
        child.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_spawn_agent_not_found(self):
        """ValueError raised when agent_name not in agent_configs."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()

        with pytest.raises(ValueError, match="not found"):
            await mgr.spawn(
                agent_name="nonexistent",
                instruction="hello",
                parent_session=parent,
                agent_configs={},
            )

    @pytest.mark.asyncio
    async def test_spawn_uses_explicit_session_id(self):
        """Explicit sub_session_id is used instead of generating one."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()
        child = _make_child_session_mock()

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ):
            result = await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="explicit-id-123",
            )

        assert result["session_id"] == "explicit-id-123"

    @pytest.mark.asyncio
    async def test_spawn_inherits_resolver_from_parent(self):
        """Module source resolver is inherited from parent session."""
        prepared = MagicMock()
        mgr = CLISpawnManager(prepared)
        parent = _make_parent_session_mock()
        child = _make_child_session_mock()

        parent_resolver = MagicMock(name="parent_resolver")
        parent.coordinator.get = MagicMock(return_value=parent_resolver)

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ):
            await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="c1",
            )

        child.coordinator.mount.assert_awaited_once_with(
            "module-source-resolver", parent_resolver,
        )

    @pytest.mark.asyncio
    async def test_spawn_falls_back_to_prepared_resolver(self):
        """Falls back to PreparedBundle resolver when parent has none."""
        prepared = MagicMock()
        prepared.resolver = MagicMock(name="prepared_resolver")
        mgr = CLISpawnManager(prepared)
        parent = _make_parent_session_mock()
        child = _make_child_session_mock()

        # Parent returns None for module-source-resolver
        parent.coordinator.get = MagicMock(return_value=None)

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ):
            await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="c1",
            )

        child.coordinator.mount.assert_awaited_once_with(
            "module-source-resolver", prepared.resolver,
        )

    @pytest.mark.asyncio
    async def test_spawn_inherits_mention_resolver(self):
        """Mention resolver is inherited from parent session."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()
        child = _make_child_session_mock()

        mention_resolver = MagicMock(name="mention_resolver")

        def _get_capability(name):
            if name == "mention_resolver":
                return mention_resolver
            return None

        parent.coordinator.get_capability = MagicMock(
            side_effect=_get_capability,
        )

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ):
            await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="c1",
            )

        child.coordinator.register_capability.assert_any_call(
            "mention_resolver", mention_resolver,
        )

    @pytest.mark.asyncio
    async def test_spawn_cancellation_propagation(self):
        """Parent cancellation is registered/unregistered on child."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()
        child = _make_child_session_mock()

        parent_cancel = MagicMock()
        parent.coordinator.cancellation = parent_cancel

        child_cancel = MagicMock()
        child.coordinator.cancellation = child_cancel

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ):
            await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="c1",
            )

        parent_cancel.register_child.assert_called_once_with(child_cancel)
        parent_cancel.unregister_child.assert_called_once_with(child_cancel)

    @pytest.mark.asyncio
    async def test_spawn_cleanup_on_execute_error(self):
        """Child session is cleaned up even if execute() raises."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()
        child = _make_child_session_mock()
        child.execute = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await mgr.spawn(
                    agent_name="explorer",
                    instruction="go",
                    parent_session=parent,
                    agent_configs={"explorer": {}},
                    sub_session_id="c1",
                )

        child.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_spawn_applies_tool_inheritance(self):
        """Tool inheritance filtering is applied to merged config."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()
        parent.config = {
            "tools": [
                {"module": "tool-filesystem"},
                {"module": "tool-task"},
            ],
            "agents": {"explorer": {}},
        }
        child = _make_child_session_mock()

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ) as mock_cls:
            await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="c1",
                tool_inheritance={"exclude_tools": ["tool-task"]},
            )

        # Verify the config passed to AmplifierSession had tool-task removed
        call_kwargs = mock_cls.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        tool_modules = [t["module"] for t in config["tools"]]
        assert "tool-filesystem" in tool_modules
        assert "tool-task" not in tool_modules

    @pytest.mark.asyncio
    async def test_spawn_applies_orchestrator_config(self):
        """Orchestrator config overrides are applied to merged config."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()
        child = _make_child_session_mock()

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ) as mock_cls:
            await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="c1",
                orchestrator_config={"min_delay_between_calls_ms": 500},
            )

        call_kwargs = mock_cls.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        orch = config["session"]["orchestrator"]["config"]
        assert orch["min_delay_between_calls_ms"] == 500

    @pytest.mark.asyncio
    async def test_spawn_applies_provider_override(self):
        """Provider override promotes the target provider to priority 0."""
        mgr = CLISpawnManager(prepared=MagicMock())
        parent = _make_parent_session_mock()
        parent.config = {
            "providers": [
                {"module": "provider-openai", "config": {"priority": 100}},
                {"module": "provider-anthropic", "config": {"priority": 100}},
            ],
            "agents": {"explorer": {}},
        }
        child = _make_child_session_mock()

        with patch(
            "amplifier_core.AmplifierSession", return_value=child,
        ) as mock_cls:
            await mgr.spawn(
                agent_name="explorer",
                instruction="go",
                parent_session=parent,
                agent_configs={"explorer": {}},
                sub_session_id="c1",
                provider_override="anthropic",
                model_override="claude-sonnet-4-20250514",
            )

        call_kwargs = mock_cls.call_args
        config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
        anthropic = [
            p for p in config["providers"] if "anthropic" in p["module"]
        ][0]
        assert anthropic["config"]["priority"] == 0
        assert anthropic["config"]["model"] == "claude-sonnet-4-20250514"


class TestCLISpawnManagerResume:
    @pytest.mark.asyncio
    async def test_resume_returns_graceful_failure(self):
        """Resume returns a failure dict (not an exception) in CLI mode."""
        mgr = CLISpawnManager(prepared=MagicMock())
        result = await mgr.resume(sub_session_id="abc-123")

        assert result["status"] == "failed"
        assert result["session_id"] == "abc-123"
        assert "not yet supported" in result["output"]
        assert result["turn_count"] == 0

    @pytest.mark.asyncio
    async def test_resume_unknown_session_uses_default_id(self):
        """Resume without sub_session_id uses 'unknown' as default."""
        mgr = CLISpawnManager(prepared=MagicMock())
        result = await mgr.resume()

        assert result["session_id"] == "unknown"
        assert result["status"] == "failed"


class TestCLISpawnManagerInit:
    def test_stores_prepared(self):
        p = MagicMock()
        mgr = CLISpawnManager(prepared=p)
        assert mgr._prepared is p
