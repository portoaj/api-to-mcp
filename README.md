# api-to-mcp

Convert any API documentation into an MCP server for Cursor in minutes.

Point it at API docs, and it scrapes, generates an OpenAPI spec, and creates a fully functional MCP server you can chat with.

## Installation

```bash
pip install apitomcp
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv pip install apitomcp
```

## Quickstart

```bash
# Install
pip install apitomcp

# First-time setup (configure your LLM provider)
apitomcp init

# Generate an MCP server from any API docs
apitomcp generate

# Install to Cursor
apitomcp install
```

Restart Cursor and start chatting with your API.

## What It Does

1. **Scrapes** API documentation (handles multi-page docs, finds all endpoints)
2. **Generates** OpenAPI 3.1 specs using LLMs (parallel processing for speed)
3. **Detects** authentication requirements (OAuth2, Bearer tokens, API keys)
4. **Creates** MCP servers that Cursor can use as tools
5. **Handles** OAuth2 token refresh automatically

## Features

- **Multi-page scraping** - Crawls linked pages to find all API endpoints
- **Parallel LLM processing** - Generates specs for many endpoints simultaneously  
- **Smart auth detection** - Analyzes docs to detect OAuth2, Bearer, or API key auth
- **OAuth2 token refresh** - Automatically refreshes expired tokens
- **Interactive CLI** - Clean prompts for configuration
- **Multiple LLM providers** - OpenRouter, Anthropic, OpenAI, Gemini

## Commands

| Command | Description |
|---------|-------------|
| `apitomcp init` | First-time setup - configure LLM provider and API key |
| `apitomcp generate` | Generate an MCP server from API documentation |
| `apitomcp list` | Show all generated servers |
| `apitomcp install` | Install servers to Cursor |
| `apitomcp delete` | Remove a generated server |
| `apitomcp auth` | Update LLM settings |
| `apitomcp output` | Export server files to current directory |
| `apitomcp run <name>` | Run a server (used by Cursor, not manually) |

## Example

```bash
$ apitomcp generate

# Enter: https://developer.spotify.com/documentation/web-api
# It scrapes 150+ pages, finds 99 API operations
# Generates OpenAPI spec in parallel
# Detects OAuth2 client credentials auth
# Prompts for your Spotify client ID and secret

$ apitomcp install

# Adds the server to Cursor's MCP config
# Restart Cursor, then ask: "Get me Taylor Swift's top tracks"
```

## How It Works

```
API Docs URL
     │
     ▼
┌─────────────┐
│   Scraper   │  BeautifulSoup + MarkItDown
│  (multi-page)│  Extracts endpoints & auth info
└─────────────┘
     │
     ▼
┌─────────────┐
│  Generator  │  LLM generates OpenAPI specs
│  (parallel) │  for each endpoint
└─────────────┘
     │
     ▼
┌─────────────┐
│  Validator  │  Validates against OpenAPI 3.1
│             │  Auto-retries on errors
└─────────────┘
     │
     ▼
┌─────────────┐
│   Runner    │  FastMCP creates tools from spec
│             │  Handles auth & token refresh
└─────────────┘
```

## Supported LLM Providers

- **OpenRouter** - Access to Claude, GPT, Gemini models
- **Anthropic** - Claude Sonnet, Haiku, Opus
- **OpenAI** - GPT-5.2, GPT-5.2 Mini
- **Gemini** - Gemini 3 Pro, Gemini 2.5 Flash

## Requirements

- Python 3.13+
- API key for one of the supported LLM providers

## Configuration

Config is stored in `~/.apitomcp/`:
- `config.json` - LLM provider settings
- `servers/<name>/` - Generated server files

## License

MIT
