"""
run_command tool — lets the agent execute any slash command by calling
its own local control API.

Replaces switch_model and smart_model tools with a single generic mechanism.
Gated behind HERMES_SELF_COMMAND env var.
"""

import json
import logging
import os
import urllib.request
import urllib.error

from tools.registry import registry

logger = logging.getLogger(__name__)

DEFAULT_PORT = 47823
PORT_FILE = os.path.expanduser("~/.hermes/control_api.port")


def _discover_port() -> int:
    try:
        with open(PORT_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return DEFAULT_PORT


def _run_command(command: str, mode: str = "queue") -> str:
    """Send a slash command to the local control API."""
    port = _discover_port()
    url = f"http://127.0.0.1:{port}/sessions/_any/message"
    body = json.dumps({"text": command, "mode": mode}).encode()

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "X-Hermes-Control": "1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return json.dumps(result, ensure_ascii=False)
    except urllib.error.HTTPError as e:
        try:
            result = json.loads(e.read())
        except Exception:
            result = {"error": f"HTTP {e.code}"}
        return json.dumps(result, ensure_ascii=False)
    except urllib.error.URLError as e:
        return json.dumps({
            "error": f"Control API not reachable: {e.reason}. "
                     "Is HERMES_CONTROL_API=1 set?"
        }, ensure_ascii=False)


def _handle_run_command(args: dict, **_kwargs) -> str:
    command = args.get("command", "").strip()
    if not command:
        return json.dumps({"error": "'command' is required"})
    mode = args.get("mode", "queue")
    if mode not in ("interrupt", "queue"):
        return json.dumps({"error": f"Invalid mode '{mode}', must be 'interrupt' or 'queue'"})
    return _run_command(command, mode=mode)


# ── Schema ──────────────────────────────────────────────────────────────

RUN_COMMAND_SCHEMA = {
    "name": "run_command",
    "description": (
        "Execute any slash command on your own session via the control API. "
        "Examples: '/model openrouter:anthropic/claude-sonnet-4', '/compact', "
        "'/autoreply off', '/reset'. Use '/help' to see all available commands."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The slash command to execute, e.g. '/model openrouter:google/gemini-2.5-flash' or '/compact'.",
            },
            "mode": {
                "type": "string",
                "enum": ["interrupt", "queue"],
                "description": "Execution mode. 'queue' (default) processes after current work; 'interrupt' cancels current work first.",
            },
        },
        "required": ["command"],
    },
}


def _check_run_command() -> bool:
    """Only available when HERMES_SELF_COMMAND is enabled."""
    return os.getenv("HERMES_SELF_COMMAND", "").lower() in ("1", "true", "yes")


# ── Registration ────────────────────────────────────────────────────────

registry.register(
    name="run_command",
    toolset="run_command",
    schema=RUN_COMMAND_SCHEMA,
    handler=_handle_run_command,
    check_fn=_check_run_command,
)
