"""
OpenSearch MCP Client — Native MCP Connection
===============================================

Connects to OpenSearch's native MCP Server (ML Commons plugin) via
Streamable HTTP, allowing tool invocation through MCP protocol instead of
raw HTTP REST.

Requires:
    - OpenSearch 3.x with `plugins.ml_commons.mcp_server_enabled: true`
    - Endpoint: https://<os-endpoint>/_plugins/_ml/mcp/sse

Tools auto-discovered via list_tools():
    - ListIndexTool, IndexMappingTool, IndexInsightTool
    - SearchIndexTool, QueryPlanningTool, PPLTool
    - VectorDBTool, RAGTool, etc.

Usage:
    client = OpenSearchMCPClient()
    await client.connect()
    tools = await client.list_tools()
    result = await client.call_tool("QueryPlanningTool", {"question": "...", "index": "anm_v003"})
    await client.disconnect()

Note:
    This client is for TOOL INVOCATION only (QPT, PPL, Search, etc.).
    Data queries (search, msearch, count) still use opensearch_client.py (HTTP REST via opensearch-py).
"""

import logging
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from mcp_servers.common.config import mcp_settings

logger = logging.getLogger("mcp.opensearch_native")


class OpenSearchMCPClient:
    """
    MCP Client for OpenSearch's native MCP Server.

    Connects via Streamable HTTP to the ML Commons MCP endpoint,
    discovers tools automatically, and provides call_tool() for invocation.
    """

    def __init__(self):
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._tools: list[dict] | None = None
        self._connected: bool = False

    @property
    def available(self) -> bool:
        """Whether the client is connected and ready."""
        return self._connected and self._session is not None

    @property
    def endpoint(self) -> str:
        """The configured OpenSearch MCP endpoint."""
        return mcp_settings.opensearch_mcp_endpoint

    async def connect(self) -> None:
        """
        Connect to OpenSearch MCP native endpoint.

        Establishes a Streamable HTTP session to:
            https://<os-endpoint>/_plugins/_ml/mcp/sse

        If endpoint is not configured, logs a warning and skips.
        """
        endpoint = mcp_settings.opensearch_mcp_endpoint
        if not endpoint:
            logger.warning(
                "OPENSEARCH_MCP_ENDPOINT not configured — "
                "OpenSearch native tools will not be available. "
                "QPT, PPL, SearchIndex, etc. require this connection."
            )
            return

        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            # Build auth headers if credentials are configured
            headers = {}
            if mcp_settings.opensearch_user and mcp_settings.opensearch_password:
                import base64
                credentials = f"{mcp_settings.opensearch_user}:{mcp_settings.opensearch_password}"
                encoded = base64.b64encode(credentials.encode()).decode()
                headers["Authorization"] = f"Basic {encoded}"

            # Connect via Streamable HTTP
            read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
                streamablehttp_client(
                    url=endpoint,
                    headers=headers if headers else None,
                    timeout=mcp_settings.opensearch_mcp_timeout,
                    sse_read_timeout=mcp_settings.opensearch_mcp_sse_read_timeout,
                )
            )

            # Initialize MCP session
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await self._session.initialize()

            # Discover tools
            tools_result = await self._session.list_tools()
            self._tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                }
                for t in tools_result.tools
            ]

            self._connected = True
            tool_names = [t["name"] for t in self._tools]
            logger.info(
                f"OpenSearch MCP connected: {endpoint} — "
                f"{len(self._tools)} tools discovered: {tool_names}"
            )

        except Exception as e:
            logger.error(f"Failed to connect to OpenSearch MCP: {e}")
            self._connected = False
            # Cleanup on failure
            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except (RuntimeError, BaseExceptionGroup, GeneratorExit):
                    pass
                except Exception:
                    pass
                self._exit_stack = None

    async def disconnect(self) -> None:
        """Close the MCP session and cleanup."""
        self._connected = False
        self._session = None
        self._tools = None
        if self._exit_stack:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except BaseExceptionGroup as eg:
                for e in eg.exceptions:
                    logger.debug(
                        "OpenSearch MCP disconnect (suppressed)",
                        error=repr(e),
                    )
            except (RuntimeError, GeneratorExit) as e:
                # mcp.client.streamable_http + anyio: wrong-task cancel scope on
                # uvicorn shutdown / --reload — known SDK noise.
                logger.debug("OpenSearch MCP disconnect (suppressed)", error=repr(e))
            except Exception as e:
                logger.debug("OpenSearch MCP disconnect", error=str(e))
            finally:
                self._exit_stack = None
        logger.info("OpenSearch MCP disconnected")

    async def list_tools(self) -> list[dict]:
        """
        Return discovered tools from the OpenSearch MCP Server.

        Returns:
            List of dicts with 'name', 'description', 'input_schema' for each tool.
            Empty list if not connected.
        """
        if not self.available or self._tools is None:
            return []
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict | None = None) -> str:
        """
        Call a tool on the OpenSearch MCP Server.

        Args:
            tool_name: Name of the tool (e.g. "QueryPlanningTool", "PPLTool")
            arguments: Tool-specific arguments

        Returns:
            Text content from the tool response.

        Raises:
            RuntimeError: If not connected or tool call fails.
        """
        if not self.available or self._session is None:
            raise RuntimeError(
                "OpenSearch MCP not connected. Call connect() first or "
                "configure OPENSEARCH_MCP_ENDPOINT."
            )

        logger.debug(f"Calling OpenSearch MCP tool: {tool_name}({arguments})")

        result = await self._session.call_tool(tool_name, arguments or {})

        # Extract text content from the result
        if result.content:
            # MCP tool results have content array — concatenate text parts
            texts = [
                part.text for part in result.content
                if hasattr(part, "text") and part.text
            ]
            response_text = "\n".join(texts) if texts else ""
        else:
            response_text = ""

        logger.debug(
            f"OpenSearch MCP tool {tool_name} returned "
            f"{len(response_text)} chars"
        )
        return response_text

    async def query_planning(self, question: str, index: str) -> str:
        """
        Convenience: call QueryPlanningTool.

        Args:
            question: Natural language question
            index: Target index (e.g. "anm_v003")

        Returns:
            Generated DSL query as string.
        """
        return await self.call_tool(
            "QueryPlanningTool",
            {"question": question, "index": index},
        )

    async def ppl_query(self, query: str) -> str:
        """
        Convenience: call PPLTool.

        Args:
            query: PPL query string (e.g. "source=anm_v003 | stats count() by nmUF")

        Returns:
            PPL query results as string.
        """
        return await self.call_tool("PPLTool", {"query": query})

    async def search_index(self, index: str, query: dict) -> str:
        """
        Convenience: call SearchIndexTool.

        Args:
            index: Target index
            query: OpenSearch DSL query body

        Returns:
            Search results as string.
        """
        import json
        return await self.call_tool(
            "SearchIndexTool",
            {"index": index, "query": json.dumps(query)},
        )


# Singleton — shared across modules that need OpenSearch MCP tools
_os_mcp_client: OpenSearchMCPClient | None = None


async def get_opensearch_mcp() -> OpenSearchMCPClient:
    """Get or create the OpenSearch MCP client singleton."""
    global _os_mcp_client
    if _os_mcp_client is None:
        _os_mcp_client = OpenSearchMCPClient()
        await _os_mcp_client.connect()
    return _os_mcp_client


async def shutdown_opensearch_mcp_singleton() -> None:
    """Close singleton client if it was created (safe for API lifespan shutdown)."""
    global _os_mcp_client
    if _os_mcp_client is None:
        return
    try:
        await _os_mcp_client.disconnect()
    except BaseExceptionGroup:
        pass
    except RuntimeError:
        pass
    except Exception:
        pass
    finally:
        _os_mcp_client = None
