"""MCP client used by the agent. One short-lived session per brief (stateless HTTP)."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from hri.config.settings import settings


class ToolUnavailable(RuntimeError):
    pass


class MCPTools:
    def __init__(self, session: ClientSession):
        self.s = session
        self.calls: list[dict] = []

    async def call(self, name: str, **args):
        try:
            res = await asyncio.wait_for(self.s.call_tool(name, args), timeout=8)
        except Exception as e:  # timeout, connection drop, server error
            raise ToolUnavailable(f"{name}: {e!r}") from e
        if res.isError:
            raise ToolUnavailable(f"{name}: {res.content}")
        self.calls.append({"tool": name, "args": args})
        if res.structuredContent is not None:
            sc = res.structuredContent
            return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
        if not texts:
            return None
        parsed = [json.loads(t) for t in texts]
        return parsed[0] if len(parsed) == 1 else parsed


@asynccontextmanager
async def mcp_tools(url: str | None = None):
    url = url or settings().mcp_url
    try:
        async with streamablehttp_client(url, timeout=5) as (read, write, _):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=5)
                yield MCPTools(session)
    except ToolUnavailable:
        raise
    except Exception as e:
        # anyio wraps failures in ExceptionGroups; normalise to one error type
        raise ToolUnavailable(f"MCP server unreachable at {url}: {e!r}") from e
