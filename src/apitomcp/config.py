"""Configuration management for apitomcp."""

import json
from pathlib import Path
from typing import TypedDict


class AuthConfig(TypedDict, total=False):
    """Authentication configuration for an API."""

    type: str  # bearer, api_key, oauth2_client_credentials, none
    header_name: str
    value_prefix: str
    env_var: str
    value: str  # Static token/key
    # OAuth2 client credentials fields
    client_id: str
    client_secret: str
    token_url: str
    scope: str
    access_token: str
    token_expires_at: str
    refresh_token: str


class ServerConfig(TypedDict):
    """Configuration for a generated MCP server."""

    server_name: str
    source_url: str
    created_at: str
    base_url: str
    auth: AuthConfig
    tool_count: int
    tool_overrides: dict


class LLMConfig(TypedDict, total=False):
    """LLM provider configuration."""

    llm_provider: str
    llm_api_key: str
    llm_model: str


def get_apitomcp_dir() -> Path:
    """Get the apitomcp config directory (~/.apitomcp)."""
    config_dir = Path.home() / ".apitomcp"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_servers_dir() -> Path:
    """Get the servers directory (~/.apitomcp/servers)."""
    servers_dir = get_apitomcp_dir() / "servers"
    servers_dir.mkdir(parents=True, exist_ok=True)
    return servers_dir


def get_config_path() -> Path:
    """Get the config file path (~/.apitomcp/config.json)."""
    return get_apitomcp_dir() / "config.json"


def load_config() -> LLMConfig:
    """Load the LLM configuration from config.json."""
    config_path = get_config_path()
    if not config_path.exists():
        return {}
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def save_config(config: LLMConfig) -> None:
    """Save the LLM configuration to config.json."""
    config_path = get_config_path()
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_server_dir(server_name: str) -> Path:
    """Get the directory for a specific server."""
    server_dir = get_servers_dir() / server_name
    server_dir.mkdir(parents=True, exist_ok=True)
    return server_dir


def load_server_config(server_name: str) -> ServerConfig | None:
    """Load a server's api2mcp.json configuration."""
    server_dir = get_servers_dir() / server_name
    config_path = server_dir / "api2mcp.json"
    if not config_path.exists():
        return None
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def save_server_config(server_name: str, config: ServerConfig) -> None:
    """Save a server's api2mcp.json configuration."""
    server_dir = get_server_dir(server_name)
    config_path = server_dir / "api2mcp.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_openapi_spec(server_name: str) -> dict | None:
    """Load a server's OpenAPI specification."""
    server_dir = get_servers_dir() / server_name
    spec_path = server_dir / "openapi.json"
    if not spec_path.exists():
        return None
    with open(spec_path, encoding="utf-8") as f:
        return json.load(f)


def save_openapi_spec(server_name: str, spec: dict) -> None:
    """Save a server's OpenAPI specification."""
    server_dir = get_server_dir(server_name)
    spec_path = server_dir / "openapi.json"
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=2)


def list_servers() -> list[str]:
    """List all generated server names."""
    servers_dir = get_servers_dir()
    return [
        d.name
        for d in servers_dir.iterdir()
        if d.is_dir() and (d / "api2mcp.json").exists()
    ]


def delete_server(server_name: str) -> bool:
    """Delete a server's configuration and files."""
    import shutil

    server_dir = get_servers_dir() / server_name
    if not server_dir.exists():
        return False
    shutil.rmtree(server_dir)
    return True
