"""Tests for the control API command validation and compact handler."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.control_api import ControlAPI


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_control_api():
    """Create a ControlAPI with a mock runner and a fake agent."""
    runner = MagicMock()
    agent = MagicMock()
    agent.external_control_commands = ["switch_model", "compact_context"]
    agent.execute_control = MagicMock(return_value={"success": True, "message": "ok"})
    runner._running_agents = {"sess1": agent}
    api = ControlAPI(runner)
    return api, agent


def _make_request(key, body):
    request = MagicMock()
    request.match_info = {"key": key}
    request.json = AsyncMock(return_value=body)
    return request


# ── Command validation tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_control_unknown_command_returns_400():
    """POST /control with an unknown command should return 400."""
    api, agent = _make_control_api()
    request = _make_request("sess1", {"command": "nonexistent"})

    resp = await api.control(request)

    assert resp.status == 400
    body = json.loads(resp.text)
    assert "error" in body
    assert "nonexistent" in body["error"]
    assert "available" in body
    assert set(body["available"]) == {"switch_model", "compact_context"}
    agent.execute_control.assert_not_called()


@pytest.mark.asyncio
async def test_control_valid_command_enqueues():
    """POST /control with a known command should enqueue and return 200."""
    api, agent = _make_control_api()
    request = _make_request("sess1", {"command": "compact_context"})

    resp = await api.control(request)

    assert resp.status == 200
    agent.execute_control.assert_called_once_with("compact_context")


@pytest.mark.asyncio
async def test_control_valid_command_with_params():
    """Extra params besides 'command' are forwarded to execute_control."""
    api, agent = _make_control_api()
    request = _make_request("sess1", {
        "command": "switch_model",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
    })

    resp = await api.control(request)

    assert resp.status == 200
    agent.execute_control.assert_called_once_with(
        "switch_model", provider="anthropic", model="claude-sonnet-4-6",
    )


@pytest.mark.asyncio
async def test_control_internal_command_rejected():
    """Internal-only commands should not be accessible via the HTTP API."""
    runner = MagicMock()
    agent = MagicMock()
    # Only switch_model is external; _internal_reset is not
    agent.external_control_commands = ["switch_model"]
    runner._running_agents = {"sess1": agent}
    api = ControlAPI(runner)

    request = _make_request("sess1", {"command": "_internal_reset"})
    resp = await api.control(request)

    assert resp.status == 400
    body = json.loads(resp.text)
    assert "_internal_reset" in body["error"]
    assert "switch_model" in body["available"]
    agent.execute_control.assert_not_called()


# ── Compact handler tests ───────────────────────────────────────────────


def test_compact_handler_uses_compress_context_cached_prompt():
    """After compact handler runs, _cached_system_prompt is set by _compress_context, not the handler."""
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._cached_system_prompt = "old prompt"
    agent.quiet_mode = True
    agent.log_prefix = ""
    # Fake compressor with enough room to compress
    agent.context_compressor = MagicMock()
    agent.context_compressor.protect_first_n = 1
    agent.context_compressor.protect_last_n = 1

    compressed_messages = [{"role": "user", "content": "summarized"}]
    new_prompt = "new system prompt from compress"

    def fake_compress(messages, system_message, task_id="default"):
        agent._cached_system_prompt = new_prompt
        return compressed_messages, new_prompt

    agent._compress_context = fake_compress

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "how are you"},
        {"role": "assistant", "content": "good"},
    ]
    agent._handle_ctrl_compact(messages, "sys msg", task_id="default")

    assert messages == compressed_messages
    assert agent._cached_system_prompt == new_prompt
