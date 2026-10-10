"""Expose Hydra's scoped tools through the official MCP protocol over stdio."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


def create_server(root: Path, *, approval_policy: str = 'deny'):
    from mcp.server import Server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
    from jsonschema import validate
    from hydra.cli.tool_binding import bind_tools
    from hydra.specialists import READ_TOOLS, catalog
    if not root.is_dir() or approval_policy not in {'deny', 'allow'}:
        raise ValueError('MCP server needs an existing root and deny/allow policy')
    tools = bind_tools(root.resolve(), approval_policy=approval_policy, include_mcp=False)
    # The readonly server does not expose browser actions or broad shell access.
    registry = {t.name: t for t in tools if approval_policy == 'allow' or t.name in READ_TOOLS}

    async def listing(ctx, params):
        rows = [Tool(name=t.name, description=t.description, input_schema=t.parameters) for t in registry.values()]
        rows.append(Tool(name='hydra_specialists', description='List available public specialist profiles.', input_schema={'type': 'object', 'properties': {}, 'additionalProperties': False}))
        return ListToolsResult(tools=rows)

    async def calling(ctx, params):
        try:
            arguments = params.arguments or {}
            if params.name == 'hydra_specialists':
                validate(arguments, {'type': 'object', 'properties': {}, 'additionalProperties': False})
                result = {'specialists': catalog()}
            else:
                tool = registry.get(params.name)
                if tool is None:
                    raise ValueError('unknown or unavailable tool')
                validate(arguments, tool.parameters)
                result = await asyncio.to_thread(tool.invoke, **arguments)
            encoded = json.dumps(result, ensure_ascii=False)
            if len(encoded.encode('utf-8')) > 1024 * 1024:
                raise ValueError('tool result exceeded 1 MiB')
            from hydra.tool_outcome import failed
            return CallToolResult(content=[TextContent(type='text', text=encoded)], structured_content=result, is_error=failed(result))
        except Exception as exc:
            return CallToolResult(content=[TextContent(type='text', text=f'Hydra tool failed: {type(exc).__name__}')], is_error=True)

    return Server('Hydra', version='1.0.0', on_list_tools=listing, on_call_tool=calling)


def serve(root: Path, *, approval_policy: str = 'deny') -> None:
    from mcp.server.stdio import stdio_server
    server = create_server(root, approval_policy=approval_policy)
    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    asyncio.run(run())
