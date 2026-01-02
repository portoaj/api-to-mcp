"""Cursor MCP configuration installer."""

import json
import sys
from pathlib import Path


def detect_cursor_config() -> Path | None:
    """
    Detect the Cursor MCP configuration file path.

    Returns:
        Path to the config file, or None if not found
    """
    possible_paths: list[Path] = []

    if sys.platform == "win32":
        # Windows paths - ~/.cursor/mcp.json is the correct location
        possible_paths = [
            Path.home() / ".cursor" / "mcp.json",
        ]
    elif sys.platform == "darwin":
        # macOS paths
        possible_paths = [
            Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "cursor.mcp" / "mcp.json",
            Path.home() / ".cursor" / "mcp.json",
        ]
    else:
        # Linux paths
        possible_paths = [
            Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "cursor.mcp" / "mcp.json",
            Path.home() / ".cursor" / "mcp.json",
        ]

    # Return the first existing path, or the first valid parent directory
    for path in possible_paths:
        if path.exists():
            return path

    # If no config exists, return the most likely path (will be created)
    for path in possible_paths:
        if path.parent.exists():
            return path

    # Create the directory for the first choice
    if possible_paths:
        possible_paths[0].parent.mkdir(parents=True, exist_ok=True)
        return possible_paths[0]

    return None


def load_cursor_config(config_path: Path) -> dict:
    """
    Load the Cursor MCP configuration.

    Args:
        config_path: Path to the mcp.json file

    Returns:
        The configuration dictionary
    """
    if not config_path.exists():
        return {"mcpServers": {}}

    with open(config_path, encoding="utf-8") as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError:
            return {"mcpServers": {}}

    # Ensure mcpServers key exists
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    return config


def save_cursor_config(config_path: Path, config: dict) -> None:
    """
    Save the Cursor MCP configuration.

    Args:
        config_path: Path to the mcp.json file
        config: The configuration dictionary to save
    """
    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def install_to_cursor(server_name: str, config_path: Path) -> None:
    """
    Install an MCP server to Cursor configuration.

    Args:
        server_name: Name of the server to install
        config_path: Path to the Cursor mcp.json file
    """
    from apitomcp.config import load_server_config

    # Load server configuration
    server_config = load_server_config(server_name)
    if not server_config:
        raise ValueError(f"Server '{server_name}' not found")

    # Load current Cursor config
    cursor_config = load_cursor_config(config_path)

    # Build the MCP server entry
    # Use python -m apitomcp run <server_name> as the command
    server_entry = {
        "command": sys.executable,
        "args": ["-m", "apitomcp", "run", server_name],
    }

    # Add environment variables for auth if configured
    auth = server_config.get("auth", {})
    if auth.get("value"):
        env_var = auth.get("env_var", f"{server_name.upper()}_API_KEY")
        server_entry["env"] = {env_var: auth["value"]}

    # Update or add the server entry (idempotent)
    cursor_config["mcpServers"][server_name] = server_entry

    # Save the updated config
    save_cursor_config(config_path, cursor_config)


def is_installed_in_cursor(server_name: str, config_path: Path | None = None) -> bool:
    """
    Check if a server is installed in Cursor configuration.

    Args:
        server_name: Name of the server to check
        config_path: Path to the Cursor mcp.json file (auto-detected if None)

    Returns:
        True if the server is installed
    """
    if config_path is None:
        config_path = detect_cursor_config()
    
    if not config_path or not config_path.exists():
        return False
    
    cursor_config = load_cursor_config(config_path)
    return server_name in cursor_config.get("mcpServers", {})


def uninstall_from_cursor(server_name: str, config_path: Path) -> bool:
    """
    Remove an MCP server from Cursor configuration.

    Args:
        server_name: Name of the server to remove
        config_path: Path to the Cursor mcp.json file

    Returns:
        True if the server was removed, False if it wasn't present
    """
    cursor_config = load_cursor_config(config_path)

    if server_name not in cursor_config.get("mcpServers", {}):
        return False

    del cursor_config["mcpServers"][server_name]
    save_cursor_config(config_path, cursor_config)

    return True
