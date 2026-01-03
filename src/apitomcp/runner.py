"""FastMCP server runtime for generated API servers."""

from __future__ import annotations

from typing import Any

import httpx
from fastmcp import FastMCP

from apitomcp.auth import AuthConfig, get_auth_headers
from apitomcp.config import ServerConfig


# Global auth config that can be refreshed
_auth_config: AuthConfig | None = None


def create_and_run_server(server_name: str, config: ServerConfig, spec: dict) -> None:
    """
    Create and run a FastMCP server from an OpenAPI specification.

    Args:
        server_name: Name of the server
        config: Server configuration from api2mcp.json
        spec: OpenAPI specification
    """
    global _auth_config

    # Create FastMCP instance
    mcp = FastMCP(
        name=server_name,
        instructions=f"MCP server for {spec.get('info', {}).get('title', server_name)} API",
    )

    # Get base URL
    base_url = config.get("base_url", "")
    if not base_url and spec.get("servers"):
        base_url = spec["servers"][0].get("url", "")

    # Setup authentication using new auth module
    auth_dict = config.get("auth", {})
    _auth_config = AuthConfig.from_dict(auth_dict)

    # Register tools from OpenAPI paths
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        for method in ["get", "post", "put", "patch", "delete"]:
            if method not in path_item:
                continue

            operation = path_item[method]
            operation_id = operation.get("operationId", f"{method}_{path.replace('/', '_')}")
            summary = operation.get("summary", "")
            description = operation.get("description", summary)

            # Create tool function with explicit parameters
            tool_fn = create_tool_function(
                base_url=base_url,
                path=path,
                method=method,
                operation=operation,
            )

            # Register with FastMCP
            mcp.tool(
                name=operation_id,
                description=description or f"{method.upper()} {path}",
            )(tool_fn)

    # Run the server (disable banner to avoid interfering with stdio MCP protocol)
    mcp.run(show_banner=False)


def openapi_type_to_python(schema: dict) -> type:
    """Convert OpenAPI type to Python type annotation."""
    openapi_type = schema.get("type", "string")

    type_mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    return type_mapping.get(openapi_type, str)


def sanitize_param_name(name: str) -> str:
    """Convert parameter name to valid Python identifier.
    
    Replaces dots, hyphens, and other invalid characters with underscores.
    """
    import re
    # Replace common invalid characters with underscores
    sanitized = re.sub(r'[.\-\[\]{}]', '_', name)
    # Ensure it starts with a letter or underscore
    if sanitized and sanitized[0].isdigit():
        sanitized = '_' + sanitized
    # Remove any remaining invalid characters
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', sanitized)
    return sanitized


def create_tool_function(
    base_url: str,
    path: str,
    method: str,
    operation: dict,
):
    """
    Create a tool function for an API operation with explicit parameters.

    Args:
        base_url: API base URL
        path: Endpoint path
        method: HTTP method
        operation: OpenAPI operation object

    Returns:
        Async function that executes the API call
    """
    # Extract parameters
    parameters = operation.get("parameters", [])
    request_body = operation.get("requestBody", {})

    # Build parameter info for the dynamic function
    # Maps sanitized Python names to original API parameter names
    param_name_map: dict[str, str] = {}
    param_info = []

    for param in parameters:
        original_name = param.get("name", "")
        sanitized_name = sanitize_param_name(original_name)
        param_name_map[sanitized_name] = original_name
        
        param_required = param.get("required", False)
        param_schema = param.get("schema", {})
        param_type = openapi_type_to_python(param_schema)
        param_default = param_schema.get("default")
        param_desc = param.get("description", "")
        param_in = param.get("in", "query")

        param_info.append(
            {
                "name": sanitized_name,
                "original_name": original_name,
                "required": param_required,
                "type": param_type,
                "default": param_default,
                "description": param_desc,
                "in": param_in,
            }
        )

    # Handle request body parameters
    body_properties = {}
    if request_body:
        body_content = request_body.get("content", {})
        for content_type, content_data in body_content.items():
            schema = content_data.get("schema", {})
            properties = schema.get("properties", {})
            required_props = schema.get("required", [])

            for original_prop_name, prop_schema in properties.items():
                sanitized_prop_name = sanitize_param_name(original_prop_name)
                param_name_map[sanitized_prop_name] = original_prop_name
                body_properties[sanitized_prop_name] = {
                    "original_name": original_prop_name,
                    "type": openapi_type_to_python(prop_schema),
                    "required": original_prop_name in required_props,
                    "description": prop_schema.get("description", ""),
                }
            break  # Only process first content type

    # Create parameter list for function signature
    func_params = []
    param_defaults = {}

    # Add required parameters first
    for p in param_info:
        if p["required"]:
            func_params.append(p["name"])

    for prop_name, prop_info_item in body_properties.items():
        if prop_info_item["required"] and prop_name not in func_params:
            func_params.append(prop_name)

    # Then optional parameters with defaults
    for p in param_info:
        if not p["required"] and p["name"] not in func_params:
            func_params.append(p["name"])
            param_defaults[p["name"]] = p["default"]

    for prop_name, prop_info_item in body_properties.items():
        if not prop_info_item["required"] and prop_name not in func_params:
            func_params.append(prop_name)
            param_defaults[prop_name] = None

    # Build function signature string
    sig_parts = []
    for param_name in func_params:
        if param_name in param_defaults:
            default_val = param_defaults[param_name]
            if default_val is None:
                sig_parts.append(f"{param_name}: str | None = None")
            elif isinstance(default_val, str):
                sig_parts.append(f"{param_name}: str = {repr(default_val)}")
            else:
                sig_parts.append(f"{param_name} = {repr(default_val)}")
        else:
            sig_parts.append(f"{param_name}: str")

    signature = ", ".join(sig_parts)

    # Build docstring (use original names for documentation)
    doc_lines = [f"Execute {method.upper()} {path}"]
    if param_info or body_properties:
        doc_lines.append("\nArgs:")
        for p in param_info:
            req = " (required)" if p["required"] else ""
            # Show sanitized name with original in parentheses if different
            if p["name"] != p["original_name"]:
                doc_lines.append(f"    {p['name']} ({p['original_name']}): {p['description']}{req}")
            else:
                doc_lines.append(f"    {p['name']}: {p['description']}{req}")
        for prop_name, prop_info_item in body_properties.items():
            req = " (required)" if prop_info_item["required"] else ""
            original = prop_info_item["original_name"]
            if prop_name != original:
                doc_lines.append(f"    {prop_name} ({original}): {prop_info_item['description']}{req}")
            else:
                doc_lines.append(f"    {prop_name}: {prop_info_item['description']}{req}")

    docstring = "\n".join(doc_lines)

    # Create a closure with the request logic
    async def _make_request(kwargs_dict: dict) -> Any:
        """Internal function that makes the actual HTTP request with auth refresh."""
        global _auth_config

        # Build the URL with path parameters
        url = base_url.rstrip("/") + path

        # Check for operation-level server override
        if operation.get("servers"):
            url = operation["servers"][0].get("url", base_url).rstrip("/") + path

        # Get auth headers (handles OAuth2 token refresh automatically)
        request_headers: dict[str, str] = {}
        if _auth_config:
            try:
                request_headers = await get_auth_headers(_auth_config)
            except Exception as e:
                # Return error instead of failing silently
                return {"error": f"Authentication failed: {str(e)}"}

        # Separate path, query, and header parameters
        query_params = {}

        for p in param_info:
            sanitized_name = p["name"]
            original_name = p["original_name"]
            param_in = p["in"]

            if sanitized_name in kwargs_dict and kwargs_dict[sanitized_name] is not None:
                value = kwargs_dict[sanitized_name]

                if param_in == "path":
                    url = url.replace(f"{{{original_name}}}", str(value))
                elif param_in == "query":
                    # Handle array types - use original name for API
                    if isinstance(value, list):
                        query_params[original_name] = ",".join(str(v) for v in value)
                    else:
                        query_params[original_name] = value
                elif param_in == "header":
                    request_headers[original_name] = str(value)

        # Handle request body
        json_body = None
        form_data = None

        if body_properties:
            # Convert sanitized names back to original names for API
            body_data = {}
            for sanitized_name, v in kwargs_dict.items():
                if sanitized_name in body_properties and v is not None:
                    original_name = body_properties[sanitized_name]["original_name"]
                    body_data[original_name] = v

            if body_data:
                # Check content type from spec
                body_content = request_body.get("content", {})
                if "application/x-www-form-urlencoded" in body_content:
                    form_data = body_data
                else:
                    json_body = body_data

        # Make the request
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                params=query_params if query_params else None,
                headers=request_headers,
                json=json_body,
                data=form_data,
            )

            # Return response
            try:
                return response.json()
            except Exception:
                return response.text

    # Create function with proper signature using exec
    if not func_params:
        # No parameters - simple function
        async def tool_function() -> Any:
            return await _make_request({})

        tool_function.__doc__ = docstring
        return tool_function

    # Build function code
    func_code = f'''
async def tool_function({signature}) -> Any:
    """{docstring}"""
    kwargs_dict = {{{", ".join(f'"{p}": {p}' for p in func_params)}}}
    return await _make_request(kwargs_dict)
'''

    # Execute to create the function
    local_ns = {"_make_request": _make_request, "Any": Any}
    exec(func_code, local_ns)

    return local_ns["tool_function"]
