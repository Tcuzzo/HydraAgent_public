#!/usr/bin/env python3
"""
MCP Client for Hydra Agent

A production-ready Model Context Protocol (MCP) client implementing:
- JSON-RPC 2.0 message encoding/decoding
- Streamable HTTP transport with SSE support
- Tool discovery and invocation
- Session management with cryptographic session IDs
- Proper initialization lifecycle

Based on MCP specification: https://modelcontextprotocol.io/specification/2025-06-18/
"""

import json
import uuid
import time
import hashlib
import secrets
from typing import Any, Dict, List, Optional, Callable, Iterator, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
import httpx
import asyncio


# Protocol version constant (latest supported)
MCP_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT_SECONDS = 30.0


class TransportType(Enum):
    """Supported MCP transport types."""
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


@dataclass
class InitializeRequest:
    """MCP initialize request structure."""
    protocolVersion: str
    capabilities: Dict[str, Any]
    clientInfo: Dict[str, str]
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InitializeResult:
    """MCP initialize response structure."""
    protocolVersion: str
    capabilities: Dict[str, Any]
    serverInfo: Dict[str, str]
    instructions: Optional[str] = None


@dataclass
class ToolDefinition:
    """Tool definition from MCP server."""
    name: str
    title: Optional[str] = None
    description: Optional[str] = None
    inputSchema: Dict[str, Any] = field(default_factory=dict)
    outputSchema: Optional[Dict[str, Any]] = None
    annotations: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolDefinition":
        return cls(
            name=data.get("name", ""),
            title=data.get("title"),
            description=data.get("description"),
            inputSchema=data.get("inputSchema", {}),
            outputSchema=data.get("outputSchema"),
            annotations=data.get("annotations")
        )


@dataclass
class ToolResult:
    """Result from a tool call."""
    content: List[Dict[str, Any]]
    structuredContent: Optional[Dict[str, Any]] = None
    isError: bool = False
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolResult":
        return cls(
            content=data.get("content", []),
            structuredContent=data.get("structuredContent"),
            isError=data.get("isError", False)
        )


class JsonRpcMessage:
    """JSON-RPC 2.0 message builder and parser."""
    
    @staticmethod
    def request(method: str, params: Optional[Dict[str, Any]] = None, 
                request_id: Optional[int] = None) -> Dict[str, Any]:
        """Build a JSON-RPC request message."""
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "id": request_id if request_id is not None else int(time.time() * 1000) % 1000000
        }
        if params is not None:
            msg["params"] = params
        return msg
    
    @staticmethod
    def notification(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build a JSON-RPC notification message (no id)."""
        msg = {
            "jsonrpc": "2.0",
            "method": method
        }
        if params is not None:
            msg["params"] = params
        return msg
    
    @staticmethod
    def response(result: Any, request_id: int) -> Dict[str, Any]:
        """Build a JSON-RPC success response."""
        return {
            "jsonrpc": "2.0",
            "result": result,
            "id": request_id
        }
    
    @staticmethod
    def error(code: int, message: str, request_id: int, 
              data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build a JSON-RPC error response."""
        err = {
            "jsonrpc": "2.0",
            "error": {
                "code": code,
                "message": message
            },
            "id": request_id
        }
        if data is not None:
            err["error"]["data"] = data
        return err
    
    @staticmethod
    def parse(raw: str) -> Dict[str, Any]:
        """Parse a JSON-RPC message from string."""
        return json.loads(raw)
    
    @staticmethod
    def serialize(msg: Dict[str, Any]) -> str:
        """Serialize a JSON-RPC message to string."""
        return json.dumps(msg, ensure_ascii=False)


class StreamableHttpTransport:
    """
    Streamable HTTP transport implementation for MCP.
    
    Supports:
    - POST requests for sending messages to server
    - SSE streams for receiving server messages
    - Session management via Mcp-Session-Id header
    - Resumability via Last-Event-ID header
    """
    
    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._client: Optional[httpx.Client] = None
        self._async_client: Optional[httpx.AsyncClient] = None
        
    def _get_client(self) -> httpx.Client:
        """Get or create synchronous HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION
                }
            )
        return self._client
    
    async def _get_async_client(self) -> httpx.AsyncClient:
        """Get or create asynchronous HTTP client."""
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION
                }
            )
        return self._async_client
    
    def _build_headers(self, include_session: bool = True) -> Dict[str, str]:
        """Build headers for requests."""
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Content-Type": "application/json"
        }
        if include_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers
    
    def send_message(self, message: Dict[str, Any]) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]:
        """
        Send a JSON-RPC message to the server.
        
        Returns either:
        - A single response dict (for notifications/responses that return 202)
        - An iterator of SSE events (for requests that stream)
        """
        client = self._get_client()
        headers = self._build_headers()
        
        try:
            response = client.post(
                self.base_url,
                headers=headers,
                json=message,
                follow_redirects=True
            )
            
            # Handle session ID from initialization
            if "Mcp-Session-Id" in response.headers:
                self.session_id = response.headers["Mcp-Session-Id"]
            
            # Handle different response types
            content_type = response.headers.get("Content-Type", "").lower()
            
            if response.status_code == 202:
                # Notification or response accepted, no body
                return {"status": "accepted", "code": 202}
            
            elif "text/event-stream" in content_type:
                # SSE stream - yield events as they arrive
                return self._parse_sse_stream(response.iter_lines())
            
            else:
                # Single JSON response
                response.raise_for_status()
                return response.json()
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404 and self.session_id:
                # Session expired, clear it
                self.session_id = None
            raise McpTransportError(f"HTTP error: {e.response.status_code}", e)
        except httpx.RequestError as e:
            raise McpTransportError(f"Request failed: {str(e)}", e)
    
    async def send_message_async(self, message: Dict[str, Any]) -> Union[Dict[str, Any], AsyncIterator[Dict[str, Any]]]:
        """Async version of send_message."""
        client = await self._get_async_client()
        headers = self._build_headers()
        
        try:
            response = await client.post(
                self.base_url,
                headers=headers,
                json=message,
                follow_redirects=True
            )
            
            if "Mcp-Session-Id" in response.headers:
                self.session_id = response.headers["Mcp-Session-Id"]
            
            content_type = response.headers.get("Content-Type", "").lower()
            
            if response.status_code == 202:
                return {"status": "accepted", "code": 202}
            elif "text/event-stream" in content_type:
                return self._parse_sse_stream_async(response.aiter_lines())
            else:
                response.raise_for_status()
                return response.json()
                
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404 and self.session_id:
                self.session_id = None
            raise McpTransportError(f"HTTP error: {e.response.status_code}", e)
        except httpx.RequestError as e:
            raise McpTransportError(f"Request failed: {str(e)}", e)
    
    def _parse_sse_stream(self, lines: Iterator[str]) -> Iterator[Dict[str, Any]]:
        """Parse SSE stream into JSON-RPC messages."""
        current_event = {"data": "", "id": None}
        
        for line in lines:
            line = line.rstrip('\n\r')
            
            if line.startswith("id:"):
                current_event["id"] = line[3:].strip()
            elif line.startswith("data:"):
                data_content = line[5:].strip()
                if current_event["data"]:
                    current_event["data"] += "\n" + data_content
                else:
                    current_event["data"] = data_content
            elif line == "" and current_event["data"]:
                # End of event, yield parsed JSON
                try:
                    yield json.loads(current_event["data"])
                except json.JSONDecodeError as e:
                    raise McpParseError(f"Failed to parse SSE event: {e}", current_event["data"])
                current_event = {"data": "", "id": None}
    
    async def _parse_sse_stream_async(self, lines: AsyncIterator[str]) -> AsyncIterator[Dict[str, Any]]:
        """Async version of SSE parsing."""
        current_event = {"data": "", "id": None}
        
        async for line in lines:
            line = line.rstrip('\n\r')
            
            if line.startswith("id:"):
                current_event["id"] = line[3:].strip()
            elif line.startswith("data:"):
                data_content = line[5:].strip()
                if current_event["data"]:
                    current_event["data"] += "\n" + data_content
                else:
                    current_event["data"] = data_content
            elif line == "" and current_event["data"]:
                try:
                    yield json.loads(current_event["data"])
                except json.JSONDecodeError as e:
                    raise McpParseError(f"Failed to parse SSE event: {e}", current_event["data"])
                current_event = {"data": "", "id": None}
    
    def start_listening_stream(self, last_event_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
        """
        Start an SSE listening stream (GET request).
        
        Used for receiving server-initiated notifications and requests.
        """
        client = self._get_client()
        headers = self._build_headers()
        
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        
        try:
            response = client.get(
                self.base_url,
                headers=headers,
                follow_redirects=True
            )
            response.raise_for_status()
            return self._parse_sse_stream(response.iter_lines())
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 405:
                raise McpTransportError("Server does not support SSE streaming", e)
            raise McpTransportError(f"Stream failed: {e.response.status_code}", e)
    
    def terminate_session(self) -> bool:
        """
        Terminate the current session via DELETE request.
        
        Returns True if successful, False if server doesn't support session termination.
        """
        if not self.session_id:
            return False
            
        client = self._get_client()
        headers = self._build_headers()
        
        try:
            response = client.delete(self.base_url, headers=headers)
            if response.status_code == 405:
                # Server doesn't allow client-initiated termination
                return False
            response.raise_for_status()
            self.session_id = None
            return True
        except httpx.RequestError:
            self.session_id = None
            return False
    
    def close(self):
        """Close the transport and release resources."""
        if self._client:
            self._client.close()
            self._client = None
    
    async def close_async(self):
        """Async version of close."""
        if self._async_client:
            await self._async_client.aclose()
            self._async_client = None


class McpClient:
    """
    Main MCP client for Hydra Agent.
    
    Provides high-level API for:
    - Connecting to MCP servers
    - Tool discovery
    - Tool invocation
    - Session lifecycle management
    """
    
    # JSON-RPC error codes
    ERROR_PARSE = -32700
    ERROR_INVALID_REQUEST = -32600
    ERROR_METHOD_NOT_FOUND = -32601
    ERROR_INVALID_PARAMS = -32602
    ERROR_INTERNAL = -32603
    
    def __init__(self, transport: StreamableHttpTransport):
        self.transport = transport
        self.initialized = False
        self.server_capabilities: Dict[str, Any] = {}
        self.server_info: Dict[str, str] = {}
        self.client_capabilities: Dict[str, Any] = {}
        self._request_counter = 0
        self._tools_cache: List[ToolDefinition] = []
        self._tools_last_cursor: Optional[str] = None
        
    def _next_id(self) -> int:
        """Generate next request ID."""
        self._request_counter += 1
        return int(time.time() * 1000) % 1000000 + self._request_counter
    
    def initialize(self, client_name: str = "HydraAgent", 
                   client_version: str = "1.0.0",
                   capabilities: Optional[Dict[str, Any]] = None) -> InitializeResult:
        """
        Initialize the MCP session.
        
        Args:
            client_name: Name identifier for this client
            client_version: Version string
            capabilities: Client capabilities to advertise
            
        Returns:
            InitializeResult with server info and negotiated capabilities
        """
        if self.initialized:
            raise McpClientError("Client already initialized")
        
        # Default client capabilities
        self.client_capabilities = capabilities or {
            "roots": {"listChanged": True},
            "sampling": {},
            "elicitation": {}
        }
        
        # Build initialize request
        init_request = InitializeRequest(
            protocolVersion=MCP_PROTOCOL_VERSION,
            capabilities=self.client_capabilities,
            clientInfo={
                "name": client_name,
                "version": client_version
            }
        )
        
        # Send request
        request_msg = JsonRpcMessage.request(
            method="initialize",
            params=init_request.to_dict(),
            request_id=self._next_id()
        )
        
        response = self.transport.send_message(request_msg)
        
        # Parse response
        if "error" in response:
            raise McpServerError(
                response["error"].get("code", self.ERROR_INTERNAL),
                response["error"].get("message", "Unknown error"),
                response["error"].get("data")
            )
        
        result_data = response.get("result", {})
        
        # Validate protocol version
        server_version = result_data.get("protocolVersion", "")
        if server_version != MCP_PROTOCOL_VERSION:
            # Version negotiation - server proposed different version
            if server_version:
                raise McpClientError(
                    f"Protocol version mismatch: requested {MCP_PROTOCOL_VERSION}, "
                    f"server responded with {server_version}"
                )
        
        # Store server info
        self.server_capabilities = result_data.get("capabilities", {})
        self.server_info = result_data.get("serverInfo", {})
        
        # Send initialized notification
        initialized_msg = JsonRpcMessage.notification(
            method="notifications/initialized"
        )
        self.transport.send_message(initialized_msg)
        
        self.initialized = True
        return InitializeResult(
            protocolVersion=server_version or MCP_PROTOCOL_VERSION,
            capabilities=self.server_capabilities,
            serverInfo=self.server_info,
            instructions=result_data.get("instructions")
        )
    
    def discover_tools(self) -> List[ToolDefinition]:
        """
        Discover available tools from the server.
        
        Returns:
            List of ToolDefinition objects
        """
        if not self.initialized:
            raise McpClientError("Client not initialized. Call initialize() first.")
        
        if "tools" not in self.server_capabilities:
            raise McpClientError("Server does not support tools capability")
        
        tools = []
        cursor = None
        
        while True:
            params = {}
            if cursor:
                params["cursor"] = cursor
            
            request_msg = JsonRpcMessage.request(
                method="tools/list",
                params=params if params else None,
                request_id=self._next_id()
            )
            
            response = self.transport.send_message(request_msg)
            
            if "error" in response:
                raise McpServerError(
                    response["error"].get("code", self.ERROR_INTERNAL),
                    response["error"].get("message", "Unknown error"),
                    response["error"].get("data")
                )
            
            result = response.get("result", {})
            tool_list = result.get("tools", [])
            
            for tool_data in tool_list:
                tools.append(ToolDefinition.from_dict(tool_data))
            
            # Check for pagination
            cursor = result.get("nextCursor")
            if not cursor:
                break
        
        self._tools_cache = tools
        return tools
    
    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        """
        Call a tool on the server.
        
        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool
            
        Returns:
            ToolResult with execution results
        """
        if not self.initialized:
            raise McpClientError("Client not initialized. Call initialize() first.")
        
        params = {"name": tool_name}
        if arguments:
            params["arguments"] = arguments
        
        request_msg = JsonRpcMessage.request(
            method="tools/call",
            params=params,
            request_id=self._next_id()
        )
        
        response = self.transport.send_message(request_msg)
        
        if "error" in response:
            raise McpServerError(
                response["error"].get("code", self.ERROR_INTERNAL),
                response["error"].get("message", "Unknown error"),
                response["error"].get("data")
            )
        
        result_data = response.get("result", {})
        return ToolResult.from_dict(result_data)
    
    def get_cached_tools(self) -> List[ToolDefinition]:
        """Return cached tool definitions from last discovery."""
        return self._tools_cache.copy()
    
    def find_tool(self, name: str) -> Optional[ToolDefinition]:
        """Find a tool by name in the cache."""
        for tool in self._tools_cache:
            if tool.name == name:
                return tool
        return None
    
    def has_capability(self, capability: str) -> bool:
        """Check if server supports a specific capability."""
        return capability in self.server_capabilities
    
    def shutdown(self):
        """Shutdown the client and terminate the session."""
        try:
            self.transport.terminate_session()
        finally:
            self.transport.close()
            self.initialized = False


class McpClientAsync(McpClient):
    """Asynchronous version of McpClient."""
    
    async def initialize(self, client_name: str = "HydraAgent",
                         client_version: str = "1.0.0",
                         capabilities: Optional[Dict[str, Any]] = None) -> InitializeResult:
        """Async initialize."""
        if self.initialized:
            raise McpClientError("Client already initialized")
        
        self.client_capabilities = capabilities or {
            "roots": {"listChanged": True},
            "sampling": {},
            "elicitation": {}
        }
        
        init_request = InitializeRequest(
            protocolVersion=MCP_PROTOCOL_VERSION,
            capabilities=self.client_capabilities,
            clientInfo={
                "name": client_name,
                "version": client_version
            }
        )
        
        request_msg = JsonRpcMessage.request(
            method="initialize",
            params=init_request.to_dict(),
            request_id=self._next_id()
        )
        
        response = await self.transport.send_message_async(request_msg)
        
        if "error" in response:
            raise McpServerError(
                response["error"].get("code", self.ERROR_INTERNAL),
                response["error"].get("message", "Unknown error"),
                response["error"].get("data")
            )
        
        result_data = response.get("result", {})
        self.server_capabilities = result_data.get("capabilities", {})
        self.server_info = result_data.get("serverInfo", {})
        
        initialized_msg = JsonRpcMessage.notification(
            method="notifications/initialized"
        )
        await self.transport.send_message_async(initialized_msg)
        
        self.initialized = True
        return InitializeResult(
            protocolVersion=result_data.get("protocolVersion", MCP_PROTOCOL_VERSION),
            capabilities=self.server_capabilities,
            serverInfo=self.server_info,
            instructions=result_data.get("instructions")
        )
    
    async def discover_tools(self) -> List[ToolDefinition]:
        """Async tool discovery."""
        if not self.initialized:
            raise McpClientError("Client not initialized")
        
        if "tools" not in self.server_capabilities:
            raise McpClientError("Server does not support tools capability")
        
        tools = []
        cursor = None
        
        while True:
            params = {}
            if cursor:
                params["cursor"] = cursor
            
            request_msg = JsonRpcMessage.request(
                method="tools/list",
                params=params if params else None,
                request_id=self._next_id()
            )
            
            response = await self.transport.send_message_async(request_msg)
            
            if "error" in response:
                raise McpServerError(
                    response["error"].get("code", self.ERROR_INTERNAL),
                    response["error"].get("message", "Unknown error"),
                    response["error"].get("data")
                )
            
            result = response.get("result", {})
            for tool_data in result.get("tools", []):
                tools.append(ToolDefinition.from_dict(tool_data))
            
            cursor = result.get("nextCursor")
            if not cursor:
                break
        
        self._tools_cache = tools
        return tools
    
    async def call_tool(self, tool_name: str, 
                        arguments: Optional[Dict[str, Any]] = None) -> ToolResult:
        """Async tool call."""
        if not self.initialized:
            raise McpClientError("Client not initialized")
        
        params = {"name": tool_name}
        if arguments:
            params["arguments"] = arguments
        
        request_msg = JsonRpcMessage.request(
            method="tools/call",
            params=params,
            request_id=self._next_id()
        )
        
        response = await self.transport.send_message_async(request_msg)
        
        if "error" in response:
            raise McpServerError(
                response["error"].get("code", self.ERROR_INTERNAL),
                response["error"].get("message", "Unknown error"),
                response["error"].get("data")
            )
        
        return ToolResult.from_dict(response.get("result", {}))
    
    async def shutdown(self):
        """Async shutdown."""
        try:
            self.transport.terminate_session()
        finally:
            await self.transport.close_async()
            self.initialized = False


# Exception classes
class McpError(Exception):
    """Base exception for MCP errors."""
    pass


class McpClientError(McpError):
    """Client-side errors (configuration, state, etc.)."""
    pass


class McpTransportError(McpError):
    """Transport layer errors (HTTP, SSE, etc.)."""
    pass


class McpParseError(McpError):
    """JSON-RPC parsing errors."""
    def __init__(self, message: str, raw_data: Optional[str] = None):
        super().__init__(message)
        self.raw_data = raw_data


class McpServerError(McpError):
    """Server-side JSON-RPC errors."""
    def __init__(self, code: int, message: str, data: Optional[Dict[str, Any]] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data


# Convenience function for quick usage
def create_client(server_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> McpClient:
    """
    Create and configure an MCP client for a server.
    
    Args:
        server_url: URL of the MCP server endpoint
        timeout: Request timeout in seconds
        
    Returns:
        Configured McpClient instance (not yet initialized)
    """
    transport = StreamableHttpTransport(server_url, timeout)
    return McpClient(transport)


async def create_client_async(server_url: str, 
                              timeout: float = DEFAULT_TIMEOUT_SECONDS) -> McpClientAsync:
    """
    Create and configure an async MCP client.
    
    Args:
        server_url: URL of the MCP server endpoint
        timeout: Request timeout in seconds
        
    Returns:
        Configured McpClientAsync instance (not yet initialized)
    """
    transport = StreamableHttpTransport(server_url, timeout)
    return McpClientAsync(transport)


if __name__ == "__main__":
    # Example usage / smoke test
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python mcp_client.py <server_url>")
        print("Example: python mcp_client.py http://localhost:8080/mcp")
        sys.exit(1)
    
    server_url = sys.argv[1]
    
    print(f"Connecting to MCP server at {server_url}...")
    client = create_client(server_url)
    
    try:
        # Initialize
        print("Initializing...")
        result = client.initialize(client_name="HydraAgent-SmokeTest")
        print(f"Connected to: {result.serverInfo.get('name', 'Unknown')} v{result.serverInfo.get('version', '?')}")
        print(f"Protocol version: {result.protocolVersion}")
        print(f"Capabilities: {list(result.capabilities.keys())}")
        
        # Discover tools
        print("\nDiscovering tools...")
        tools = client.discover_tools()
        print(f"Found {len(tools)} tools:")
        for tool in tools:
            print(f"  - {tool.name}: {tool.description or 'No description'}")
        
        print("\n✓ Smoke test passed!")
        
    except McpError as e:
        print(f"✗ MCP error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        sys.exit(1)
    finally:
        client.shutdown()
