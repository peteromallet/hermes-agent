"""
Local-only HTTP control API for the Hermes gateway.

Runs alongside the gateway as a background aiohttp server on 127.0.0.1.
Provides programmatic control over live agent sessions — model switching,
session listing, etc.

External tools (e.g., desloppify) can call these endpoints to influence
a running Hermes agent without needing direct process access.

Default port: 47823 (override with HERMES_CONTROL_PORT env var).
"""

import json
import logging
import os

from aiohttp import web

logger = logging.getLogger(__name__)

DEFAULT_PORT = 47823


def _get_port() -> int:
    return int(os.getenv("HERMES_CONTROL_PORT", str(DEFAULT_PORT)))


def _json_response(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
        status=status,
    )


class ControlAPI:
    """Lightweight control API that holds a reference to the GatewayRunner."""

    def __init__(self, gateway_runner):
        self.runner = gateway_runner
        self.app = web.Application()
        self._setup_routes()
        self._site = None

    def _setup_routes(self):
        self.app.router.add_get("/sessions", self.list_sessions)
        self.app.router.add_get("/sessions/{key}", self.get_session)
        self.app.router.add_post("/sessions/{key}/switch-model", self.switch_model)
        self.app.router.add_post("/sessions/{key}/control", self.control)
        self.app.router.add_get("/health", self.health)

    # ── Helpers ─────────────────────────────────────────────────────────

    def _resolve_agent(self, key: str):
        """Look up a running agent by session key.  '_any' returns the first."""
        if key == "_any":
            if not self.runner._running_agents:
                return None
            key = next(iter(self.runner._running_agents))
        return self.runner._running_agents.get(key)

    # ── Endpoints ────────────────────────────────────────────────────────

    async def health(self, request: web.Request) -> web.Response:
        return _json_response({"status": "ok"})

    async def list_sessions(self, request: web.Request) -> web.Response:
        """List all sessions, marking which have a live (running) agent."""
        sessions = []
        # Running agents (actively processing a message)
        for key, agent in self.runner._running_agents.items():
            sessions.append({
                "session_key": key,
                "status": "running",
                "model": getattr(agent, "model", None),
                "provider": getattr(agent, "provider", None),
            })
        return _json_response({"sessions": sessions})

    async def get_session(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        agent = self.runner._running_agents.get(key)
        if not agent:
            return _json_response(
                {"error": f"No running agent for session '{key}'"},
                status=404,
            )
        return _json_response({
            "session_key": key,
            "status": "running",
            "model": getattr(agent, "model", None),
            "provider": getattr(agent, "provider", None),
        })

    async def switch_model(self, request: web.Request) -> web.Response:
        """Switch the model on a live agent session.

        POST body: {"provider": "...", "model": "...", "reason": "..."}

        If session_key is "_any", switches the first running agent found.
        """
        key = request.match_info["key"]

        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON body"}, status=400)

        provider = body.get("provider", "").strip()
        model = body.get("model", "").strip()

        if not provider or not model:
            return _json_response(
                {"error": "Both 'provider' and 'model' are required"},
                status=400,
            )

        agent = self._resolve_agent(key)
        if not agent:
            return _json_response(
                {"error": f"No running agent for '{key}'"},
                status=404,
            )

        reason = body.get("reason", "external control API")
        logger.info(
            "Control API: switch_model session=%s → %s/%s (reason: %s)",
            key, provider, model, reason,
        )

        agent.enqueue_control("switch_model", provider=provider, model=model)
        return _json_response({
            "success": True,
            "message": "Model switch queued — will apply before next API call",
            "current": {"provider": getattr(agent, "provider", ""), "model": getattr(agent, "model", "")},
            "target": {"provider": provider, "model": model},
        })

    async def control(self, request: web.Request) -> web.Response:
        """Generic control endpoint — enqueue any registered command.

        POST body: {"command": "...", ...params}
        """
        key = request.match_info["key"]

        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "Invalid JSON body"}, status=400)

        command = body.get("command", "").strip()
        if not command:
            return _json_response({"error": "'command' is required"}, status=400)

        agent = self._resolve_agent(key)
        if agent is None:
            return _json_response({"error": f"No running agent for '{key}'"}, status=404)

        # Validate against externally-exposed commands only
        available = getattr(agent, 'external_control_commands', [])
        if command not in available:
            return _json_response(
                {"error": f"Unknown command: '{command}'", "available": available},
                status=400,
            )

        params = {k: v for k, v in body.items() if k != "command"}
        agent.enqueue_control(command, **params)
        return _json_response({"success": True, "message": f"'{command}' queued"})

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        port = _get_port()
        app_runner = web.AppRunner(self.app, access_log=None)
        await app_runner.setup()
        self._site = web.TCPSite(app_runner, "127.0.0.1", port)
        try:
            await self._site.start()
            logger.info("Control API listening on http://127.0.0.1:%d", port)
            # Write port file so external tools can discover us
            self._write_port_file(port)
        except OSError as e:
            logger.warning("Control API failed to bind port %d: %s", port, e)

    async def stop(self):
        if self._site:
            await self._site.stop()
            self._remove_port_file()
            logger.info("Control API stopped")

    @staticmethod
    def _write_port_file(port: int):
        path = os.path.expanduser("~/.hermes/control_api.port")
        try:
            with open(path, "w") as f:
                f.write(str(port))
        except OSError:
            pass

    @staticmethod
    def _remove_port_file():
        path = os.path.expanduser("~/.hermes/control_api.port")
        try:
            os.remove(path)
        except OSError:
            pass
