"""Tests for CLI auto-reply: _CLIRunner shim, _generate_autoreply_text,
and /autoreply command handling.

Mirrors tests/gateway/test_autoreply_command.py for the CLI code paths.
"""

from queue import Queue
from unittest.mock import MagicMock, patch

import pytest

from agent.autoreply import (
    CLI_INPUT_PREFIX,
    _DEFAULT_MAX_TURNS,
)
from cli import _CLIRunner


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_cli_stub(autoreply_config=None, agent_running=False):
    """Create a minimal HermesCLI-like stub for unit testing."""
    cli = MagicMock()
    cli._autoreply_config = autoreply_config
    cli._agent_running = agent_running
    cli._pending_input = Queue()
    cli._interrupt_queue = Queue()
    cli.conversation_history = []
    return cli


# ── _CLIRunner tests ─────────────────────────────────────────────────────


class TestCLIRunner:
    """Tests for the _CLIRunner shim used by ControlAPI."""

    def test_inject_message_interrupt_when_agent_running(self):
        cli = _make_cli_stub(agent_running=True)
        runner = _CLIRunner(MagicMock(), "sess1", cli)

        runner.inject_message("sess1", "hello", interrupt=True)

        assert cli._interrupt_queue.get_nowait() == "hello"
        assert cli._pending_input.empty()

    def test_inject_message_queue_when_agent_not_running(self):
        cli = _make_cli_stub(agent_running=False)
        runner = _CLIRunner(MagicMock(), "sess1", cli)

        runner.inject_message("sess1", "hello", interrupt=True)

        assert cli._pending_input.get_nowait() == "hello"
        assert cli._interrupt_queue.empty()

    def test_inject_message_queue_mode(self):
        cli = _make_cli_stub(agent_running=True)
        runner = _CLIRunner(MagicMock(), "sess1", cli)

        runner.inject_message("sess1", "hello", interrupt=False)

        assert cli._pending_input.get_nowait() == "hello"
        assert cli._interrupt_queue.empty()

    def test_get_session_info_no_config(self):
        cli = _make_cli_stub(autoreply_config=None)
        runner = _CLIRunner(MagicMock(), "sess1", cli)

        info = runner.get_session_info("sess1")

        assert info["autoreply"]["enabled"] is False
        assert info["autoreply"]["prompt"] is None

    def test_get_session_info_with_config(self):
        cfg = {
            "prompt": "Ask follow-up questions",
            "model": None,
            "max_turns": _DEFAULT_MAX_TURNS,
            "turn_count": 3,
        }
        cli = _make_cli_stub(autoreply_config=cfg)
        runner = _CLIRunner(MagicMock(), "sess1", cli)

        info = runner.get_session_info("sess1")

        assert info["autoreply"]["enabled"] is True
        assert info["autoreply"]["prompt"] == "Ask follow-up questions"
        assert info["autoreply"]["turn_count"] == 3
        assert info["autoreply"]["max_turns"] == _DEFAULT_MAX_TURNS

    def test_running_agents_contains_session(self):
        agent = MagicMock()
        runner = _CLIRunner(agent, "sess1", MagicMock())

        assert "sess1" in runner._running_agents
        assert runner._running_agents["sess1"] is agent


# ── _generate_autoreply_text tests ───────────────────────────────────────


class TestGenerateAutoreplyText:
    """Tests for HermesCLI._generate_autoreply_text."""

    def _make_cli_for_autoreply(self, config=None, history=None):
        """Build a HermesCLI stub with just enough for _generate_autoreply_text."""
        from cli import HermesCLI
        cli = object.__new__(HermesCLI)
        cli._autoreply_config = config
        cli.conversation_history = history or []
        return cli

    def test_returns_none_when_not_configured(self):
        cli = self._make_cli_for_autoreply(config=None)
        assert cli._generate_autoreply_text() is None

    def test_returns_none_and_clears_config_when_cap_reached(self):
        config = {
            "prompt": "test", "model": None,
            "max_turns": 5, "turn_count": 5,
        }
        cli = self._make_cli_for_autoreply(config=config)

        with patch("cli._cprint"):
            result = cli._generate_autoreply_text()

        assert result is None
        assert cli._autoreply_config is None

    def test_literal_mode_returns_prompt(self):
        config = {
            "prompt": "continue", "model": None,
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0, "literal": True,
        }
        cli = self._make_cli_for_autoreply(config=config)

        result = cli._generate_autoreply_text()

        assert result == "continue"
        assert cli._autoreply_config["turn_count"] == 1

    def test_llm_mode_calls_prepare_and_extract(self):
        config = {
            "prompt": "Ask follow-up questions",
            "model": None,
            "max_turns": _DEFAULT_MAX_TURNS,
            "turn_count": 2,
        }
        history = [
            {"role": "user", "content": "Tell me about Python"},
            {"role": "assistant", "content": "Python is great."},
        ]
        cli = self._make_cli_for_autoreply(config=config, history=history)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "What about its type system?"

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response) as mock_llm:
            result = cli._generate_autoreply_text()

        assert result == "What about its type system?"
        assert config["turn_count"] == 3

        # Verify LLM was called with correct kwargs
        call_kwargs = mock_llm.call_args[1]
        assert call_kwargs["task"] == "autoreply"
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_tokens"] == 1024

    def test_model_override_passed_to_llm(self):
        config = {
            "prompt": "test", "model": "openai/gpt-4o-mini",
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        cli = self._make_cli_for_autoreply(config=config, history=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "reply"

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response) as mock_llm:
            cli._generate_autoreply_text()

        assert mock_llm.call_args[1]["model"] == "openai/gpt-4o-mini"

    def test_empty_response_returns_none(self):
        config = {
            "prompt": "test", "model": None,
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        cli = self._make_cli_for_autoreply(config=config, history=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ])

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        with patch("agent.auxiliary_client.call_llm", return_value=mock_response):
            result = cli._generate_autoreply_text()

        assert result is None
        # turn_count should NOT have incremented
        assert config["turn_count"] == 0


# ── _handle_autoreply_command tests ──────────────────────────────────────


class TestHandleAutoreplyCommand:
    """Tests for HermesCLI._handle_autoreply_command."""

    def _make_cli(self, config=None):
        from cli import HermesCLI
        cli = object.__new__(HermesCLI)
        cli._autoreply_config = config
        return cli

    def test_enable_with_instructions(self):
        cli = self._make_cli()
        with patch("cli._cprint"):
            cli._handle_autoreply_command("/autoreply Ask follow-up questions")

        cfg = cli._autoreply_config
        assert cfg is not None
        assert cfg["prompt"] == "Ask follow-up questions"
        assert cfg["max_turns"] == _DEFAULT_MAX_TURNS
        assert cfg["turn_count"] == 0

    def test_disable(self):
        cli = self._make_cli(config={
            "prompt": "test", "model": None,
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        })
        with patch("cli._cprint"):
            cli._handle_autoreply_command("/autoreply off")

        assert cli._autoreply_config is None

    def test_disable_when_not_active(self):
        cli = self._make_cli()
        with patch("cli._cprint") as mock_print:
            cli._handle_autoreply_command("/autoreply off")
        assert "not active" in mock_print.call_args[0][0].lower()

    def test_status_when_active(self):
        cli = self._make_cli(config={
            "prompt": "Ask questions", "model": None,
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 3,
        })
        with patch("cli._cprint") as mock_print:
            cli._handle_autoreply_command("/autoreply")
        output = mock_print.call_args[0][0]
        assert "active" in output.lower() or "Active" in output

    def test_status_when_not_active(self):
        cli = self._make_cli()
        with patch("cli._cprint") as mock_print:
            cli._handle_autoreply_command("/autoreply")
        assert "not active" in mock_print.call_args[0][0].lower()

    def test_set_max_turns(self):
        cli = self._make_cli(config={
            "prompt": "test", "model": None,
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        })
        with patch("cli._cprint"):
            cli._handle_autoreply_command("/autoreply max 25")
        assert cli._autoreply_config["max_turns"] == 25

    def test_literal_mode(self):
        cli = self._make_cli()
        with patch("cli._cprint"):
            cli._handle_autoreply_command("/autoreply --literal continue")

        cfg = cli._autoreply_config
        assert cfg["literal"] is True
        assert cfg["prompt"] == "continue"

    def test_error_message(self):
        cli = self._make_cli()
        with patch("cli._cprint") as mock_print:
            cli._handle_autoreply_command("/autoreply --literal")
        assert "Usage" in mock_print.call_args[0][0]


# ── CLI_INPUT_PREFIX usage tests ─────────────────────────────────────────


class TestCLIInputPrefix:
    """Tests for CLI_INPUT_PREFIX constant usage in the input loop."""

    def test_prefix_value(self):
        assert CLI_INPUT_PREFIX == "[autoreply]"

    def test_prefix_stripping(self):
        """Simulate the prefix-stripping logic from the CLI input loop."""
        user_input = f"{CLI_INPUT_PREFIX}What about types?"
        if user_input.startswith(CLI_INPUT_PREFIX):
            user_input = user_input[len(CLI_INPUT_PREFIX):]
        assert user_input == "What about types?"

    def test_real_message_does_not_strip(self):
        user_input = "What about types?"
        if user_input.startswith(CLI_INPUT_PREFIX):
            user_input = user_input[len(CLI_INPUT_PREFIX):]
        assert user_input == "What about types?"

    def test_turn_counter_reset_on_real_message(self):
        """Real messages reset turn counter; prefixed messages don't."""
        config = {"turn_count": 5}

        # Real message resets
        user_input = "hello"
        if not user_input.startswith(CLI_INPUT_PREFIX):
            config["turn_count"] = 0
        assert config["turn_count"] == 0

        # Autoreply message does not reset
        config["turn_count"] = 5
        user_input = f"{CLI_INPUT_PREFIX}generated reply"
        if not user_input.startswith(CLI_INPUT_PREFIX):
            config["turn_count"] = 0
        assert config["turn_count"] == 5
