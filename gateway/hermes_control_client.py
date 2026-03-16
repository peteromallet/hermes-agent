"""
Hermes control client — lightweight module for external tools to control
a running Hermes instance (CLI or gateway).

Usage:
    from gateway.hermes_control_client import HermesControl

    ctl = HermesControl()  # auto-discovers port from ~/.hermes/control_api.port

    # Send a message (interrupt mode — stops current work)
    ctl.send_message("Focus on this instead")

    # Queue a message (processed after current work finishes)
    ctl.send_message("When you're done, also check logs", mode="queue")

    # Send a slash command
    ctl.send_message("/reset")
    ctl.send_message("/model openrouter:anthropic/claude-sonnet-4")

    # List running sessions
    sessions = ctl.list_sessions()

    # List available commands
    commands = ctl.list_commands()

Zero dependencies beyond stdlib (uses urllib, not requests/aiohttp).
"""

import json
import os
import urllib.request
import urllib.error
from typing import Optional

DEFAULT_PORT = 47823
PORT_FILE = os.path.expanduser("~/.hermes/control_api.port")


class HermesControl:
    """Client for the Hermes control API."""

    def __init__(self, host: str = "127.0.0.1", port: Optional[int] = None):
        if port is None:
            port = self._discover_port()
        self.base_url = f"http://{host}:{port}"

    @staticmethod
    def _discover_port() -> int:
        try:
            with open(PORT_FILE) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return DEFAULT_PORT

    def _request(self, method: str, path: str, body: dict = None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body else None
        headers = {"X-Hermes-Control": "1"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())
        except urllib.error.URLError as e:
            return {"error": f"Cannot reach Hermes control API: {e.reason}"}

    # ── Read-only endpoints ────────────────────────────────────────────

    def health(self) -> dict:
        """Check if the control API is running."""
        return self._request("GET", "/health")

    def list_sessions(self) -> dict:
        """List all running agent sessions."""
        return self._request("GET", "/sessions")

    def get_session(self, session_key: str = "_any") -> dict:
        """Get info about a specific session."""
        return self._request("GET", f"/sessions/{session_key}")

    def list_commands(self) -> dict:
        """List available slash commands."""
        return self._request("GET", "/commands")

    # ── Message injection ──────────────────────────────────────────────

    def send_message(
        self,
        text: str,
        session_key: str = "_any",
        mode: str = "interrupt",
    ) -> dict:
        """Send a message (or /command) into a running session.

        Args:
            text: Message text or slash command (e.g., "hello", "/reset").
            session_key: Session to target. "_any" targets the first running session.
            mode: "interrupt" (default) stops current work; "queue" waits for it to finish.
        """
        return self._request("POST", f"/sessions/{session_key}/message", {
            "text": text,
            "mode": mode,
        })

    # ── Convenience methods ────────────────────────────────────────────

    def switch_model(self, provider: str, model: str, **kw) -> dict:
        """Switch model via /model command."""
        return self.send_message(f"/model {provider}:{model}", **kw)

    def compact_context(self, **kw) -> dict:
        """Trigger context compaction."""
        return self.send_message("/compact", **kw)

    def reset(self, **kw) -> dict:
        """Reset conversation history."""
        return self.send_message("/reset", **kw)

    def stop(self, **kw) -> dict:
        """Stop the running agent."""
        return self.send_message("/stop", **kw)

    def is_autoreply_enabled(self, session_key: str = "_any") -> bool:
        """Check if autoreply is enabled for a session."""
        info = self.get_session(session_key)
        return bool(info.get("autoreply", {}).get("enabled"))

    def is_available(self) -> bool:
        """Check if a Hermes instance with control API is reachable."""
        try:
            return self.health().get("status") == "ok"
        except Exception:
            return False
