"""
Unified MCP Provider — Single Protocol for All MCP Servers
============================================================

Connects to ALL MCP Servers via JSON-RPC over HTTP (Streamable HTTP transport),
using httpx directly to avoid cancel scope bugs in mcp.client.streamable_http
with Python 3.12 + asyncio.

Architecture:
    LangGraph → UnifiedMCPProvider → httpx (JSON-RPC 2.0)
        → OpenSearch MCP Nativo (/_plugins/_ml/mcp)
        → MCP Server Jazidas (:8110/mcp)
        → MCP Server Empresas (:8111/mcp)
        → MCP Server Geo (:8112/mcp)
"""

import asyncio
import logging
from typing import Any

import httpx

from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("mcp.provider")


class MCPServerConnection:
    """Represents a connection to a single MCP Server via JSON-RPC HTTP."""

    def __init__(self, name: str, url: str, headers: dict | None = None):
        self.name = name
        self.url = url
        self.headers = headers or {}
        self.tools: list[dict] = []
        self.connected: bool = False
        self._client: httpx.AsyncClient | None = None
        self._req_id: int = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _rpc(self, method: str, params: dict | None = None) -> Any:
        """Send a single JSON-RPC 2.0 request and return the result."""
        if self._client is None:
            raise RuntimeError(f"MCP Server '{self.name}' client not initialised")

        payload: dict = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params:
            payload["params"] = params

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }

        resp = await self._client.post(self.url, json=payload, headers=headers)
        resp.raise_for_status()

        # Streamable HTTP may return SSE — read first data line
        ct = resp.headers.get("content-type", "")
        if "text/event-stream" in ct:
            body = await _read_sse_json(resp)
        else:
            body = resp.json()

        if "error" in body and body["error"]:
            raise RuntimeError(f"JSON-RPC error from '{self.name}': {body['error']}")
        return body.get("result")

    async def _notify(self, method: str, params: dict | None = None) -> None:
        """
        Send JSON-RPC 2.0 notification (no ``id``).

        ``notifications/initialized`` must not use :meth:`_rpc` — including ``id``
        makes some Streamable HTTP MCP stacks try to parse the message as a
        ``ClientRequest`` and log spurious Pydantic validation warnings.
        """
        if self._client is None:
            return
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.headers,
        }
        try:
            resp = await self._client.post(self.url, json=payload, headers=headers)
            resp.raise_for_status()
            ct = resp.headers.get("content-type", "")
            if "text/event-stream" in ct:
                await _read_sse_json(resp)
            else:
                try:
                    body = resp.json()
                except Exception:
                    return
                if isinstance(body, dict) and body.get("error"):
                    logger.debug(
                        "MCP notify %r on %r: %s", method, self.name, body["error"]
                    )
        except Exception as e:
            logger.debug("MCP notify %r on %r failed: %s", method, self.name, e)

    async def connect(self) -> bool:
        """Open client, initialize session, list tools."""
        try:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    mcp_settings.mcp_jsonrpc_timeout,
                    connect=mcp_settings.mcp_connect_timeout,
                ),
                follow_redirects=True,
            )
            # initialize
            await self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mineralradar-api", "version": "1.0.0"},
            })
            await self._notify("notifications/initialized")

            # list tools
            result = await self._rpc("tools/list")
            raw_tools = result.get("tools", []) if result else []
            self.tools = [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "input_schema": t.get("inputSchema", {}),
                }
                for t in raw_tools
            ]
            self.connected = True
            logger.info(
                f"MCP Server '{self.name}' connected — "
                f"{len(self.tools)} tools: {[t['name'] for t in self.tools]}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to connect to MCP Server '{self.name}' ({self.url}): {e}")
            await self.close()
            return False

    async def call_tool(self, tool_name: str, arguments: dict | None = None) -> str:
        """Call a tool and return text result."""
        result = await self._rpc("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
        if not result:
            return ""
        content = result.get("content", [])
        texts = [p.get("text", "") for p in content if p.get("type") == "text"]
        return "\n".join(texts)

    async def close(self):
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
        self.connected = False
        self.tools = []


async def _read_sse_json(resp: httpx.Response) -> dict:
    """Parse the first JSON object from an SSE response body."""
    import json
    raw = await resp.aread()
    text = raw.decode("utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                try:
                    return json.loads(data)
                except Exception:
                    pass
    # fallback: try parsing entire body as JSON
    try:
        return resp.json()
    except Exception:
        return {}


class UnifiedMCPProvider:
    """
    Unified provider that connects to ALL MCP Servers via JSON-RPC HTTP.

    Uses httpx directly (no mcp.client.streamable_http) to avoid
    Python 3.12 asyncio cancel scope incompatibilities.
    """

    def __init__(self, mcp_servers: dict[str, str] | None = None):
        if mcp_servers is None:
            mcp_servers = self._build_from_config()

        self._servers: dict[str, MCPServerConnection] = {
            name: MCPServerConnection(name, url, self._get_auth_headers(name, url))
            for name, url in mcp_servers.items()
            if url
        }

    @staticmethod
    def _build_from_config() -> dict[str, str]:
        servers: dict[str, str] = {}
        if mcp_settings.opensearch_mcp_endpoint:
            servers["opensearch"] = mcp_settings.opensearch_mcp_endpoint
        if mcp_settings.mcp_jazidas_url:
            servers["jazidas"] = mcp_settings.mcp_jazidas_url
        if mcp_settings.mcp_empresas_url:
            servers["empresas"] = mcp_settings.mcp_empresas_url
        if mcp_settings.mcp_geo_url:
            servers["geo"] = mcp_settings.mcp_geo_url
        return servers

    @staticmethod
    def _get_auth_headers(server_name: str, url: str = "") -> dict[str, str]:
        headers: dict[str, str] = {}

        # OpenSearch: Basic Auth
        if server_name == "opensearch":
            if mcp_settings.opensearch_user and mcp_settings.opensearch_password:
                import base64
                creds = f"{mcp_settings.opensearch_user}:{mcp_settings.opensearch_password}"
                encoded = base64.b64encode(creds.encode()).decode()
                headers["Authorization"] = f"Basic {encoded}"

        # Python MCPs accessed via host.docker.internal need Host: localhost:PORT
        # FastMCP 1.26+ validates the Host header and rejects non-localhost by default
        if "host.docker.internal" in url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            headers["Host"] = f"localhost:{port}"

        return headers

    async def connect(self) -> None:
        tasks = [server.connect() for server in self._servers.values()]
        await asyncio.gather(*tasks, return_exceptions=True)

        connected = [n for n, s in self._servers.items() if s.connected]
        total_tools = sum(len(s.tools) for s in self._servers.values() if s.connected)
        logger.info(
            f"UnifiedMCPProvider ready — "
            f"{len(connected)}/{len(self._servers)} servers connected, "
            f"{total_tools} total tools"
        )

    async def disconnect(self) -> None:
        tasks = [s.close() for s in self._servers.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, Exception):
                # e.g. CancelledError during uvicorn shutdown — ignore
                continue
            if isinstance(r, Exception):
                logger.debug("MCP server close", error=repr(r))
        logger.info("UnifiedMCPProvider disconnected")

    # ── Tool discovery ──────────────────────────────────────────────────────

    def get_all_tools(self) -> list[dict]:
        all_tools = []
        for server in self._servers.values():
            if not server.connected:
                continue
            for t in server.tools:
                all_tools.append({
                    "server": server.name,
                    "name": f"{server.name}__{t['name']}",
                    "original_name": t["name"],
                    "description": t["description"],
                    "input_schema": t["input_schema"],
                })
        return all_tools

    def get_server_tools(self, server_name: str) -> list[dict]:
        s = self._servers.get(server_name)
        return s.tools if s and s.connected else []

    def status(self) -> dict:
        return {
            name: {
                "url": s.url,
                "connected": s.connected,
                "tool_count": len(s.tools),
                "tools": [t["name"] for t in s.tools] if s.connected else [],
            }
            for name, s in self._servers.items()
        }

    # ── Tool invocation ─────────────────────────────────────────────────────

    async def call_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        server = self._servers.get(server_name)
        if not server:
            raise RuntimeError(f"MCP Server '{server_name}' not configured")
        if not server.connected:
            ok = await server.connect()
            if not ok:
                raise RuntimeError(f"MCP Server '{server_name}' could not reconnect")
        return await server.call_tool(tool_name, arguments)

    async def call_prefixed_tool(
        self, prefixed_name: str, arguments: dict[str, Any] | None = None
    ) -> str:
        parts = prefixed_name.split("__", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid prefixed tool name '{prefixed_name}'")
        return await self.call_tool(parts[0], parts[1], arguments)
