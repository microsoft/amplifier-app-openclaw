"""Tests for the CLI entry point."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from amplifier_app_openclaw.cli import cli


runner = CliRunner()


class TestHelp:
    """--help returns 0 for every command / subcommand."""

    def test_root_help(self):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "Amplifier" in result.output

    def test_run_help(self):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--bundle" in result.output

    def test_cost_help(self):
        result = runner.invoke(cli, ["cost", "--help"])
        assert result.exit_code == 0
        assert "--period" in result.output

    def test_bundles_help(self):
        result = runner.invoke(cli, ["bundles", "--help"])
        assert result.exit_code == 0

    def test_bundles_list_help(self):
        result = runner.invoke(cli, ["bundles", "list", "--help"])
        assert result.exit_code == 0

    def test_bundles_add_help(self):
        result = runner.invoke(cli, ["bundles", "add", "--help"])
        assert result.exit_code == 0


class TestVersion:
    def test_version(self):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestRunCommand:
    def test_run_outputs_json(self):
        mock_result = {"response": "hello", "usage": {}, "status": "completed"}
        with patch("amplifier_app_openclaw.runner.run_task", new_callable=AsyncMock, return_value=mock_result):
            result = runner.invoke(cli, ["run", "test prompt"])
        assert result.exit_code == 0
        # stdout may contain stderr info lines from Click mixed output; extract JSON
        lines = result.output.strip().splitlines()
        json_str = "\n".join(l for l in lines if not l.startswith("[info]"))
        parsed = json.loads(json_str)
        assert parsed["response"] == "hello"

    def test_run_missing_prompt(self):
        result = runner.invoke(cli, ["run"])
        assert result.exit_code != 0

    def test_run_custom_options(self):
        mock_result = {"response": "ok", "usage": {}, "status": "completed"}
        with patch("amplifier_app_openclaw.runner.run_task", new_callable=AsyncMock, return_value=mock_result) as mock_run:
            result = runner.invoke(cli, ["run", "--bundle", "custom", "--timeout", "60", "hello"])
        assert result.exit_code == 0
        mock_run.assert_called_once()
        call_kw = mock_run.call_args
        assert call_kw.kwargs.get("bundle_name") or call_kw[1].get("bundle_name") == "custom"


class TestCostCommand:
    def test_cost_outputs_json(self):
        mock_report = {"period": "day", "total_cost": 0.0, "task_count": 0}
        with patch("amplifier_app_openclaw.cost.generate_cost_report", return_value=mock_report):
            result = runner.invoke(cli, ["cost"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "total_cost" in parsed

    def test_cost_invalid_period(self):
        result = runner.invoke(cli, ["cost", "--period", "century"])
        assert result.exit_code != 0
