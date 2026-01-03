"""apitomcp - Convert any API documentation into an MCP server."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("apitomcp")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"
