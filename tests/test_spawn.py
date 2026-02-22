"""Tests for CLISpawnManager."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from amplifier_app_openclaw.spawn import CLISpawnManager


@pytest.fixture
def mgr():
    return CLISpawnManager(prepared=MagicMock())


class TestCLISpawnManager:
    @pytest.mark.asyncio
    async def test_spawn_raises(self, mgr):
        with pytest.raises(NotImplementedError, match="spawn"):
            await mgr.spawn(task="do something")

    @pytest.mark.asyncio
    async def test_resume_raises(self, mgr):
        with pytest.raises(NotImplementedError, match="resume"):
            await mgr.resume(session_id="abc")

    def test_stores_prepared(self, mgr):
        assert mgr._prepared is not None
