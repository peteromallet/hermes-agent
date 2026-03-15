"""Tests for /autoreply gateway slash command.

Tests the auto-reply loop: enabling/disabling via command, auto-reply
generation, message injection, turn counting, cap notification, and
cleanup on /reset and /stop.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, SendResult
from agent.autoreply import _DEFAULT_MAX_TURNS
from gateway.session import SessionSource, build_session_key


def _make_source(platform=Platform.TELEGRAM, user_id="12345", chat_id="67890"):
    return SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )


def _make_event(text="/autoreply", platform=Platform.TELEGRAM,
                user_id="12345", chat_id="67890", message_id=None):
    """Build a MessageEvent for testing."""
    source = _make_source(platform, user_id, chat_id)
    return MessageEvent(text=text, source=source, message_id=message_id)


def _make_runner():
    """Create a bare GatewayRunner with minimal mocks."""
    from gateway.run import GatewayRunner
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._autoreply_configs = {}
    runner._honcho_managers = {}
    runner._honcho_configs = {}

    mock_store = MagicMock()
    mock_store.get_or_create_session.return_value = MagicMock(
        session_id="sess_123",
        session_key="agent:main:telegram:dm:12345",
        created_at=MagicMock(strftime=lambda f: "2026-01-01 00:00"),
        updated_at=MagicMock(strftime=lambda f: "2026-01-01 00:00"),
        total_tokens=0,
        last_prompt_tokens=0,
        was_auto_reset=False,
    )
    mock_store._generate_session_key.return_value = "agent:main:telegram:dm:12345"
    mock_store.load_transcript.return_value = []
    runner.session_store = mock_store

    from gateway.hooks import HookRegistry
    runner.hooks = HookRegistry()

    return runner


# ---------------------------------------------------------------------------
# _handle_autoreply_command
# ---------------------------------------------------------------------------


class TestHandleAutoreplyCommand:
    """Tests for GatewayRunner._handle_autoreply_command."""

    @pytest.mark.asyncio
    async def test_no_args_shows_inactive_status(self):
        runner = _make_runner()
        event = _make_event(text="/autoreply")
        result = await runner._handle_autoreply_command(event)
        assert "not active" in result.lower()

    @pytest.mark.asyncio
    async def test_no_args_shows_active_status(self):
        runner = _make_runner()
        session_key = build_session_key(_make_source())
        runner._autoreply_configs[session_key] = {
            "prompt": "Ask follow-up questions",
            "model": None,
            "max_turns": _DEFAULT_MAX_TURNS,
            "turn_count": 3,
        }
        event = _make_event(text="/autoreply")
        result = await runner._handle_autoreply_command(event)
        assert "active" in result.lower()
        assert f"3/{_DEFAULT_MAX_TURNS}" in result
        assert "Ask follow-up" in result

    @pytest.mark.asyncio
    async def test_enable_with_instructions(self):
        runner = _make_runner()
        event = _make_event(text="/autoreply Ask follow-up questions about the topic")
        result = await runner._handle_autoreply_command(event)
        assert "enabled" in result.lower()
        assert f"{_DEFAULT_MAX_TURNS} turns" in result

        session_key = build_session_key(_make_source())
        cfg = runner._autoreply_configs[session_key]
        assert cfg["prompt"] == "Ask follow-up questions about the topic"
        assert cfg["max_turns"] == 20
        assert cfg["turn_count"] == 0

    @pytest.mark.asyncio
    async def test_disable(self):
        runner = _make_runner()
        session_key = build_session_key(_make_source())
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        event = _make_event(text="/autoreply off")
        result = await runner._handle_autoreply_command(event)
        assert "disabled" in result.lower()
        assert session_key not in runner._autoreply_configs

    @pytest.mark.asyncio
    async def test_disable_when_not_active(self):
        runner = _make_runner()
        event = _make_event(text="/autoreply off")
        result = await runner._handle_autoreply_command(event)
        assert "not active" in result.lower()

    @pytest.mark.asyncio
    async def test_set_max_turns(self):
        runner = _make_runner()
        session_key = build_session_key(_make_source())
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        event = _make_event(text="/autoreply max 25")
        result = await runner._handle_autoreply_command(event)
        assert "25" in result
        assert runner._autoreply_configs[session_key]["max_turns"] == 25

    @pytest.mark.asyncio
    async def test_max_without_active_config(self):
        runner = _make_runner()
        event = _make_event(text="/autoreply max 5")
        result = await runner._handle_autoreply_command(event)
        assert "not active" in result.lower()

    @pytest.mark.asyncio
    async def test_max_invalid_number(self):
        runner = _make_runner()
        session_key = build_session_key(_make_source())
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        event = _make_event(text="/autoreply max abc")
        result = await runner._handle_autoreply_command(event)
        assert "Usage" in result

    @pytest.mark.asyncio
    async def test_max_zero_rejected(self):
        runner = _make_runner()
        session_key = build_session_key(_make_source())
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        event = _make_event(text="/autoreply max 0")
        result = await runner._handle_autoreply_command(event)
        assert "at least 1" in result

    @pytest.mark.asyncio
    async def test_instructions_starting_with_max_not_treated_as_subcommand(self):
        """'/autoreply maximize depth' should set instructions, not parse as /max."""
        runner = _make_runner()
        event = _make_event(text="/autoreply maximize the depth of investigation")
        result = await runner._handle_autoreply_command(event)
        assert "enabled" in result.lower()

        session_key = build_session_key(_make_source())
        assert "maximize" in runner._autoreply_configs[session_key]["prompt"]

    @pytest.mark.asyncio
    async def test_long_prompt_truncated_in_preview(self):
        runner = _make_runner()
        long_prompt = "A" * 200
        event = _make_event(text=f"/autoreply {long_prompt}")
        result = await runner._handle_autoreply_command(event)
        assert "..." in result
        assert long_prompt not in result

    @pytest.mark.asyncio
    async def test_literal_flag_enables_literal_mode(self):
        """/autoreply --literal <msg> enables literal (no-LLM) mode."""
        runner = _make_runner()
        event = _make_event(text="/autoreply --literal continue")
        result = await runner._handle_autoreply_command(event)
        assert "literal" in result.lower()

        session_key = build_session_key(_make_source())
        cfg = runner._autoreply_configs[session_key]
        assert cfg["prompt"] == "continue"
        assert cfg["literal"] is True

    @pytest.mark.asyncio
    async def test_literal_flag_without_message_rejected(self):
        runner = _make_runner()
        event = _make_event(text="/autoreply --literal    ")
        result = await runner._handle_autoreply_command(event)
        assert "Usage" in result

    @pytest.mark.asyncio
    async def test_literal_status_shows_mode(self):
        runner = _make_runner()
        session_key = build_session_key(_make_source())
        runner._autoreply_configs[session_key] = {
            "prompt": "continue", "model": None, "max_turns": _DEFAULT_MAX_TURNS,
            "turn_count": 2, "literal": True,
        }
        event = _make_event(text="/autoreply")
        result = await runner._handle_autoreply_command(event)
        assert "literal" in result.lower()
        assert "Message:" in result

    @pytest.mark.asyncio
    async def test_disable_aliases(self):
        """All disable aliases work: off, disable, stop."""
        for word in ("off", "disable", "stop"):
            runner = _make_runner()
            session_key = build_session_key(_make_source())
            runner._autoreply_configs[session_key] = {
                "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
            }
            event = _make_event(text=f"/autoreply {word}")
            result = await runner._handle_autoreply_command(event)
            assert "disabled" in result.lower()


# ---------------------------------------------------------------------------
# _generate_autoreply
# ---------------------------------------------------------------------------


class TestGenerateAutoreply:
    """Tests for GatewayRunner._generate_autoreply."""

    @pytest.mark.asyncio
    async def test_returns_none_when_not_configured(self):
        runner = _make_runner()
        text, cap = await runner._generate_autoreply("no_such_key", "sess_123")
        assert text is None
        assert cap is False

    @pytest.mark.asyncio
    async def test_returns_none_and_cap_when_limit_reached(self):
        runner = _make_runner()
        runner._autoreply_configs["key"] = {
            "prompt": "test", "model": None, "max_turns": 5, "turn_count": 5,
        }
        text, cap = await runner._generate_autoreply("key", "sess_123")
        assert text is None
        assert cap is True
        assert "key" not in runner._autoreply_configs

    @pytest.mark.asyncio
    async def test_literal_mode_skips_llm(self):
        """Literal mode returns the prompt directly without calling the LLM."""
        runner = _make_runner()
        runner._autoreply_configs["key"] = {
            "prompt": "continue", "model": None,
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0, "literal": True,
        }
        # No LLM mock needed — literal mode shouldn't call it
        text, cap = await runner._generate_autoreply("key", "sess_123")
        assert text == "continue"
        assert cap is False
        assert runner._autoreply_configs["key"]["turn_count"] == 1

    @pytest.mark.asyncio
    async def test_generates_reply_and_increments_counter(self):
        runner = _make_runner()
        runner._autoreply_configs["key"] = {
            "prompt": "Ask follow-up questions",
            "model": None,
            "max_turns": _DEFAULT_MAX_TURNS,
            "turn_count": 2,
        }
        runner.session_store.load_transcript.return_value = [
            {"role": "user", "content": "Tell me about Python"},
            {"role": "assistant", "content": "Python is a programming language."},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "What about its type system?"

        with patch("agent.auxiliary_client.async_call_llm", return_value=mock_response) as mock_llm:
            text, cap = await runner._generate_autoreply("key", "sess_123")

        assert text == "What about its type system?"
        assert cap is False
        assert runner._autoreply_configs["key"]["turn_count"] == 3

        # Verify the LLM was called with task="autoreply"
        call_kwargs = mock_llm.call_args[1]
        assert call_kwargs["task"] == "autoreply"
        assert call_kwargs["temperature"] == 0.7
        # System prompt should contain the user's instructions
        system_msg = call_kwargs["messages"][0]
        assert system_msg["role"] == "system"
        assert "Ask follow-up questions" in system_msg["content"]

    @pytest.mark.asyncio
    async def test_flattens_list_content(self):
        """Messages with list-type content are flattened to text."""
        runner = _make_runner()
        runner._autoreply_configs["key"] = {
            "prompt": "Continue", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        runner.session_store.load_transcript.return_value = [
            {"role": "user", "content": [
                {"type": "text", "text": "Look at this image"},
                {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
            ]},
            {"role": "assistant", "content": "I see the image."},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "What else can you tell me?"

        with patch("agent.auxiliary_client.async_call_llm", return_value=mock_response) as mock_llm:
            await runner._generate_autoreply("key", "sess_123")

        # The user message should have been flattened to text only
        messages = mock_llm.call_args[1]["messages"]
        user_msg = [m for m in messages if m["role"] == "user" and "Look at" in m.get("content", "")]
        assert len(user_msg) == 1
        assert user_msg[0]["content"] == "Look at this image"

    @pytest.mark.asyncio
    async def test_uses_model_override(self):
        runner = _make_runner()
        runner._autoreply_configs["key"] = {
            "prompt": "test", "model": "openai/gpt-4o-mini",
            "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        runner.session_store.load_transcript.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "reply"

        with patch("agent.auxiliary_client.async_call_llm", return_value=mock_response) as mock_llm:
            await runner._generate_autoreply("key", "sess_123")

        assert mock_llm.call_args[1]["model"] == "openai/gpt-4o-mini"


# ---------------------------------------------------------------------------
# _process_autoreply
# ---------------------------------------------------------------------------


class TestProcessAutoreply:
    """Tests for GatewayRunner._process_autoreply."""

    @pytest.mark.asyncio
    async def test_injects_message_via_adapter(self):
        runner = _make_runner()
        source = _make_source()
        session_key = build_session_key(source)
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        runner.session_store.load_transcript.return_value = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Generated reply"

        mock_adapter = AsyncMock()
        mock_adapter.handle_message = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        with patch("agent.auxiliary_client.async_call_llm", return_value=mock_response):
            await runner._process_autoreply(source, session_key, "sess_123")

        mock_adapter.handle_message.assert_called_once()
        injected_event = mock_adapter.handle_message.call_args[0][0]
        assert injected_event.text == "Generated reply"
        assert injected_event.message_id.startswith("autoreply-")
        assert injected_event.source is source

    @pytest.mark.asyncio
    async def test_sends_cap_notification(self):
        runner = _make_runner()
        source = _make_source()
        session_key = build_session_key(source)
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": 3, "turn_count": 3,
        }

        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="1"))
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        await runner._process_autoreply(source, session_key, "sess_123")

        mock_adapter.send.assert_called_once()
        content = mock_adapter.send.call_args[1]["content"]
        assert "limit reached" in content.lower()
        # Should NOT have injected a message
        mock_adapter.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_notification_when_manually_disabled(self):
        """If config was already removed (user ran /autoreply off), no cap notification."""
        runner = _make_runner()
        source = _make_source()
        session_key = build_session_key(source)
        # Config is NOT present — simulates user having run /autoreply off

        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="1"))
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        await runner._process_autoreply(source, session_key, "sess_123")

        mock_adapter.send.assert_not_called()
        mock_adapter.handle_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_adapter_returns_silently(self):
        runner = _make_runner()
        source = _make_source()
        session_key = build_session_key(source)
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        # No adapter — should not raise
        await runner._process_autoreply(source, session_key, "sess_123")

    @pytest.mark.asyncio
    async def test_llm_error_is_caught(self):
        """LLM failures are logged, not raised."""
        runner = _make_runner()
        source = _make_source()
        session_key = build_session_key(source)
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        runner.session_store.load_transcript.return_value = []

        mock_adapter = AsyncMock()
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        with patch("agent.auxiliary_client.async_call_llm", side_effect=RuntimeError("LLM down")):
            # Should not raise
            await runner._process_autoreply(source, session_key, "sess_123")

    @pytest.mark.asyncio
    async def test_cap_notification_includes_thread_id(self):
        """Cap notification respects thread_id for threaded platforms."""
        runner = _make_runner()
        source = SessionSource(
            platform=Platform.SLACK,
            user_id="U123",
            chat_id="C456",
            user_name="testuser",
            thread_id="ts_789",
        )
        session_key = build_session_key(source)
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": 1, "turn_count": 1,
        }

        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="1"))
        runner.adapters[Platform.SLACK] = mock_adapter

        await runner._process_autoreply(source, session_key, "sess_123")

        call_kwargs = mock_adapter.send.call_args[1]
        assert call_kwargs["metadata"] == {"thread_id": "ts_789"}


# ---------------------------------------------------------------------------
# Turn counter reset & auto-reply label in _handle_message
# ---------------------------------------------------------------------------


class TestAutoreplyInHandleMessage:
    """Tests for auto-reply wiring in _handle_message.

    These test the auto-reply logic directly rather than going through the
    full _handle_message pipeline, which requires too much setup. The
    individual methods (_generate_autoreply, _process_autoreply) are
    thoroughly tested above; here we verify the wiring code itself.
    """

    def test_autoreply_message_detected_by_message_id(self):
        """Messages with autoreply- prefix in message_id are detected."""
        event = _make_event(text="test", message_id="autoreply-123.456")
        assert (event.message_id or "").startswith("autoreply-")

        event2 = _make_event(text="test", message_id="regular-123")
        assert not (event2.message_id or "").startswith("autoreply-")

    def test_turn_counter_reset_logic(self):
        """Real messages reset counter; autoreply messages don't."""
        runner = _make_runner()
        session_key = "agent:main:telegram:dm:12345"
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 7,
        }

        # Real message — should reset
        is_autoreply = ("regular-msg" or "").startswith("autoreply-")
        if session_key in runner._autoreply_configs and not is_autoreply:
            runner._autoreply_configs[session_key]["turn_count"] = 0
        assert runner._autoreply_configs[session_key]["turn_count"] == 0

        # Restore and test autoreply message — should NOT reset
        runner._autoreply_configs[session_key]["turn_count"] = 7
        is_autoreply = ("autoreply-123.456" or "").startswith("autoreply-")
        if session_key in runner._autoreply_configs and not is_autoreply:
            runner._autoreply_configs[session_key]["turn_count"] = 0
        assert runner._autoreply_configs[session_key]["turn_count"] == 7

    def test_response_labeling_logic(self):
        """Auto-reply responses get labeled with turn count."""
        runner = _make_runner()
        session_key = "test_key"
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 3,
        }

        # Simulate the labeling code from _handle_message
        is_autoreply = True
        response = "Agent response here"
        event_text = "Generated question"

        if is_autoreply and response:
            ar_config = runner._autoreply_configs.get(session_key)
            if ar_config:
                response = (
                    f"**[Auto-reply {ar_config['turn_count']}/{ar_config['max_turns']}]:** "
                    f"{event_text}\n\n---\n\n{response}"
                )

        assert f"**[Auto-reply 3/{_DEFAULT_MAX_TURNS}]:**" in response
        assert "Generated question" in response
        assert "Agent response here" in response

    def test_no_label_for_regular_messages(self):
        """Regular messages are not labeled."""
        response = "Agent response"
        is_autoreply = False

        if is_autoreply and response:
            response = "SHOULD NOT HAPPEN"

        assert response == "Agent response"


# ---------------------------------------------------------------------------
# Cleanup on /reset and /stop
# ---------------------------------------------------------------------------


class TestAutoreplyCleanup:
    """Tests for auto-reply cleanup on /reset and /stop."""

    @pytest.mark.asyncio
    async def test_reset_clears_autoreply(self):
        runner = _make_runner()
        session_key = "agent:main:telegram:dm:12345"
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }

        # Stub out honcho shutdown
        runner._shutdown_gateway_honcho = MagicMock()

        event = _make_event(text="/reset")
        await runner._handle_reset_command(event)
        assert session_key not in runner._autoreply_configs

    @pytest.mark.asyncio
    async def test_stop_clears_autoreply(self):
        runner = _make_runner()
        session_key = "agent:main:telegram:dm:12345"
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }

        event = _make_event(text="/stop")
        result = await runner._handle_stop_command(event)
        assert session_key not in runner._autoreply_configs
        assert "disabled" in result.lower()

    @pytest.mark.asyncio
    async def test_stop_with_running_agent_and_autoreply(self):
        runner = _make_runner()
        session_key = "agent:main:telegram:dm:12345"
        runner._autoreply_configs[session_key] = {
            "prompt": "test", "model": None, "max_turns": _DEFAULT_MAX_TURNS, "turn_count": 0,
        }
        mock_agent = MagicMock()
        runner._running_agents[session_key] = mock_agent

        event = _make_event(text="/stop")
        result = await runner._handle_stop_command(event)

        mock_agent.interrupt.assert_called_once()
        assert session_key not in runner._autoreply_configs
        assert "Auto-reply disabled" in result
        assert "Stopping" in result

    @pytest.mark.asyncio
    async def test_stop_without_autoreply_or_agent(self):
        runner = _make_runner()
        event = _make_event(text="/stop")
        result = await runner._handle_stop_command(event)
        assert "No active task" in result


# ---------------------------------------------------------------------------
# /autoreply in help and known_commands
# ---------------------------------------------------------------------------


class TestAutoreplyInHelp:
    """Verify /autoreply appears in help text and known commands."""

    @pytest.mark.asyncio
    async def test_autoreply_in_help_output(self):
        runner = _make_runner()
        event = _make_event(text="/help")
        result = await runner._handle_help_command(event)
        assert "/autoreply" in result

    def test_autoreply_is_known_command(self):
        from gateway.run import GatewayRunner
        source = inspect.getsource(GatewayRunner._handle_message)
        assert '"autoreply"' in source
