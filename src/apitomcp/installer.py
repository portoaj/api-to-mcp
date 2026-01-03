"""MCP configuration installer for Cursor and Claude Desktop."""

import json
import os
import sys
from pathlib import Path
from typing import NamedTuple


class InstallTarget(NamedTuple):
    """Represents an MCP client installation target."""
    name: str
    config_path: Path
    display_name: str


# --- Generic MCP Config Functions ---

def load_mcp_config(config_path: Path) -> dict:
    """
    Load an MCP configuration file.

    Args:
        config_path: Path to the config JSON file

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

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    return config


def save_mcp_config(config_path: Path, config: dict) -> None:
    """
    Save an MCP configuration file.

    Args:
        config_path: Path to the config JSON file
        config: The configuration dictionary to save
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def build_server_entry(server_name: str, server_config: dict) -> dict:
    """
    Build the MCP server entry for a config file.

    Args:
        server_name: Name of the server
        server_config: The server's api2mcp.json configuration

    Returns:
        Dictionary suitable for mcpServers entry
    """
    server_entry = {
        "command": sys.executable,
        "args": ["-m", "apitomcp", "run", server_name],
    }

    auth = server_config.get("auth", {})
    if auth.get("value"):
        env_var = auth.get("env_var", f"{server_name.upper()}_API_KEY")
        server_entry["env"] = {env_var: auth["value"]}

    return server_entry


def install_to_target(server_name: str, config_path: Path) -> None:
    """
    Install an MCP server to a target configuration.

    Args:
        server_name: Name of the server to install
        config_path: Path to the MCP config file
    """
    from apitomcp.config import load_server_config

    server_config = load_server_config(server_name)
    if not server_config:
        raise ValueError(f"Server '{server_name}' not found")

    mcp_config = load_mcp_config(config_path)
    server_entry = build_server_entry(server_name, server_config)
    mcp_config["mcpServers"][server_name] = server_entry
    save_mcp_config(config_path, mcp_config)


def is_installed_in_target(server_name: str, config_path: Path) -> bool:
    """
    Check if a server is installed in a target configuration.

    Args:
        server_name: Name of the server to check
        config_path: Path to the MCP config file

    Returns:
        True if the server is installed
    """
    if not config_path.exists():
        return False

    mcp_config = load_mcp_config(config_path)
    return server_name in mcp_config.get("mcpServers", {})


def uninstall_from_target(server_name: str, config_path: Path) -> bool:
    """
    Remove an MCP server from a target configuration.

    Args:
        server_name: Name of the server to remove
        config_path: Path to the MCP config file

    Returns:
        True if the server was removed, False if it wasn't present
    """
    mcp_config = load_mcp_config(config_path)

    if server_name not in mcp_config.get("mcpServers", {}):
        return False

    del mcp_config["mcpServers"][server_name]
    save_mcp_config(config_path, mcp_config)

    return True


# --- Cursor-specific Functions ---

def detect_cursor_config() -> Path | None:
    """
    Detect the Cursor MCP configuration file path.

    Returns:
        Path to the config file, or None if not found
    """
    possible_paths: list[Path] = []

    if sys.platform == "win32":
        possible_paths = [
            Path.home() / ".cursor" / "mcp.json",
        ]
    elif sys.platform == "darwin":
        possible_paths = [
            Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "cursor.mcp" / "mcp.json",
            Path.home() / ".cursor" / "mcp.json",
        ]
    else:
        possible_paths = [
            Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "cursor.mcp" / "mcp.json",
            Path.home() / ".cursor" / "mcp.json",
        ]

    for path in possible_paths:
        if path.exists():
            return path

    for path in possible_paths:
        if path.parent.exists():
            return path

    if possible_paths:
        possible_paths[0].parent.mkdir(parents=True, exist_ok=True)
        return possible_paths[0]

    return None


def load_cursor_config(config_path: Path) -> dict:
    """Load the Cursor MCP configuration."""
    return load_mcp_config(config_path)


def save_cursor_config(config_path: Path, config: dict) -> None:
    """Save the Cursor MCP configuration."""
    save_mcp_config(config_path, config)


def install_to_cursor(server_name: str, config_path: Path) -> None:
    """Install an MCP server to Cursor configuration."""
    install_to_target(server_name, config_path)


def is_installed_in_cursor(server_name: str, config_path: Path | None = None) -> bool:
    """Check if a server is installed in Cursor configuration."""
    if config_path is None:
        config_path = detect_cursor_config()

    if not config_path:
        return False

    return is_installed_in_target(server_name, config_path)


def uninstall_from_cursor(server_name: str, config_path: Path) -> bool:
    """Remove an MCP server from Cursor configuration."""
    return uninstall_from_target(server_name, config_path)


# --- Claude Desktop-specific Functions ---

def detect_claude_desktop_config() -> Path | None:
    """
    Detect the Claude Desktop MCP configuration file path.

    Returns:
        Path to the config file, or None if not found
    """
    possible_paths: list[Path] = []

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            possible_paths = [
                Path(appdata) / "Claude" / "claude_desktop_config.json",
            ]
    elif sys.platform == "darwin":
        possible_paths = [
            Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        ]
    else:
        possible_paths = [
            Path.home() / ".config" / "Claude" / "claude_desktop_config.json",
        ]

    for path in possible_paths:
        if path.exists():
            return path

    for path in possible_paths:
        if path.parent.exists():
            return path

    return None


def install_to_claude_desktop(server_name: str, config_path: Path) -> None:
    """Install an MCP server to Claude Desktop configuration."""
    install_to_target(server_name, config_path)


def is_installed_in_claude_desktop(server_name: str, config_path: Path | None = None) -> bool:
    """Check if a server is installed in Claude Desktop configuration."""
    if config_path is None:
        config_path = detect_claude_desktop_config()

    if not config_path:
        return False

    return is_installed_in_target(server_name, config_path)


def uninstall_from_claude_desktop(server_name: str, config_path: Path) -> bool:
    """Remove an MCP server from Claude Desktop configuration."""
    return uninstall_from_target(server_name, config_path)


# --- Multi-target Functions ---

def detect_available_targets() -> list[InstallTarget]:
    """
    Detect all available MCP client installation targets.

    Returns:
        List of available installation targets
    """
    targets = []

    cursor_path = detect_cursor_config()
    if cursor_path:
        targets.append(InstallTarget(
            name="cursor",
            config_path=cursor_path,
            display_name=f"Cursor ({cursor_path})",
        ))

    claude_path = detect_claude_desktop_config()
    if claude_path:
        targets.append(InstallTarget(
            name="claude_desktop",
            config_path=claude_path,
            display_name=f"Claude Desktop ({claude_path})",
        ))

    return targets
