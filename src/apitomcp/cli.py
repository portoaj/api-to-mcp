"""CLI commands for apitomcp."""

from __future__ import annotations

import asyncio
import datetime
import warnings
from typing import TYPE_CHECKING

import typer

# Suppress litellm/pydantic warnings globally (they fire after asyncio cleanup)
warnings.filterwarnings(
    "ignore",
    message="coroutine 'close_litellm_async_clients' was never awaited",
    category=RuntimeWarning,
)
warnings.filterwarnings(
    "ignore",
    message="Pydantic serializer warnings",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message="Enable tracemalloc",
    category=RuntimeWarning,
)


def _get_event_loop():
    """Get or create a reusable event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def _run_async(coro):
    """Run async code in a reusable event loop."""
    loop = _get_event_loop()
    return loop.run_until_complete(coro)

from apitomcp import __version__

app = typer.Typer(
    name="apitomcp",
    help="Convert any API documentation into an MCP server.",
    no_args_is_help=True,
    add_completion=False,
)

# Provider configurations with model options
# Model strings must match what litellm expects for each provider
# See: https://docs.litellm.ai/docs/providers
PROVIDERS = {
    "OpenRouter": {
        "env_var": "OPENROUTER_API_KEY",
        "models": [
            # OpenRouter models need "openrouter/" prefix for litellm
            ("openrouter/anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5 (recommended)"),
            ("openrouter/anthropic/claude-opus-4.5", "Claude Opus 4.5"),
            ("openrouter/openai/gpt-5.2", "GPT-5.2"),
            ("openrouter/openai/gpt-5-mini", "GPT-5 Mini"),
            ("openrouter/google/gemini-3-flash-preview", "Gemini 3 Flash"),
            ("openrouter/google/gemini-3-pro-preview", "Gemini 3 Pro Preview"),
        ],
    },
    "Anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "models": [
            # Anthropic models: check https://docs.anthropic.com/en/docs/models
            ("claude-sonnet-4.5-20251022", "Claude Sonnet 4.5 (recommended)"),
            ("claude-haiku-4.5-20251022", "Claude Haiku 4.5 (faster, cheaper)"),
            ("claude-opus-4.5-20251022", "Claude Opus 4.5"),
        ],
    },
    "OpenAI": {
        "env_var": "OPENAI_API_KEY",
        "models": [
            # OpenAI models: check https://platform.openai.com/docs/models
            ("gpt-5.2", "GPT-5.2 (recommended)"),
            ("gpt-5.2-mini", "GPT-5.2 Mini (faster, cheaper)"),
        ],
    },
    "Gemini": {
        "env_var": "GEMINI_API_KEY",
        "models": [
            # Gemini models need "gemini/" prefix for litellm
            ("gemini/gemini-3-pro-preview", "Gemini 3 Pro Preview (recommended)"),
            ("gemini/gemini-2.5-flash", "Gemini 2.5 Flash"),
        ],
    },
}


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        from apitomcp.ui import console

        console.print(f"apitomcp version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """apitomcp - Convert any API documentation into an MCP server."""
    pass


def _select_provider(current_provider: str | None = None, allow_skip: bool = False) -> str | None:
    """Prompt user to select an LLM provider with arrow key navigation.
    
    If allow_skip is True and current_provider is set, adds a skip option.
    Returns None if user chooses to skip.
    """
    from InquirerPy.base.control import Choice

    from apitomcp.ui import prompt_choice

    providers = list(PROVIDERS.keys())
    
    if allow_skip and current_provider and current_provider in PROVIDERS:
        choices = [
            Choice(value="__skip__", name=f"Keep current ({current_provider})"),
            *providers,
        ]
        selected = prompt_choice("Select your LLM provider", choices, default="__skip__")
        return None if selected == "__skip__" else selected
    
    default = current_provider if current_provider in PROVIDERS else providers[0]
    return prompt_choice("Select your LLM provider", providers, default=default)


def _select_model(provider: str, current_model: str | None = None, allow_skip: bool = False) -> str | None:
    """Prompt user to select a model or enter a custom one with arrow key navigation.
    
    If allow_skip is True and current_model is set, adds a skip option.
    Returns None if user chooses to skip.
    """
    from InquirerPy.base.control import Choice

    from apitomcp.ui import prompt_choice, prompt_text

    provider_info = PROVIDERS[provider]
    models = provider_info["models"]

    # Build choices with display names and values
    choices = []
    
    # Add skip option if allowed and there's a current model
    if allow_skip and current_model:
        choices.append(Choice(value="__skip__", name=f"Keep current ({current_model})"))
    
    choices.extend([
        Choice(value=model_id, name=f"{model_id} ({desc})")
        for model_id, desc in models
    ])
    choices.append(Choice(value="__custom__", name="Enter custom model"))

    # Determine default
    if allow_skip and current_model:
        default = "__skip__"
    elif current_model in [m[0] for m in models]:
        default = current_model
    else:
        default = models[0][0]

    selected = prompt_choice(
        f"Select a model for {provider}",
        choices,
        default=default,
    )

    if selected == "__skip__":
        return None

    if selected == "__custom__":
        custom = prompt_text("Enter the model identifier (e.g., anthropic/claude-sonnet-4)")
        return custom if custom else models[0][0]

    return selected


class ValidationError(Exception):
    """Raised when API key validation fails with a clear message."""

    pass


def _validate_api_key(provider: str, api_key: str, model: str) -> None:
    """Validate an API key by making a test request."""
    import os

    import litellm

    # Suppress litellm's noisy informational output
    litellm.suppress_debug_info = True

    # Set the API key via environment variable
    env_var = PROVIDERS[provider]["env_var"]
    os.environ[env_var] = api_key

    try:
        litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=5,
        )
    except litellm.AuthenticationError as e:
        raise ValidationError(f"Invalid API key for {provider}. Check your key and try again.") from e
    except litellm.NotFoundError as e:
        raise ValidationError(
            f"Model '{model}' not found.\n"
            f"  Check the model ID is correct, or select 'Enter custom model' to specify a different one."
        ) from e
    except litellm.BadRequestError as e:
        error_msg = str(e).strip()
        if not error_msg or "Exception -" in error_msg and error_msg.endswith("-"):
            raise ValidationError(
                f"Request rejected by {provider}.\n"
                f"  Possible causes:\n"
                f"  - Model '{model}' may not exist or isn't available\n"
                f"  - API key may not have access to this model\n"
                f"  - Rate limit or quota exceeded\n"
                f"  Try a different model or check your {provider} dashboard."
            ) from e
        raise ValidationError(f"{provider} error: {error_msg}") from e
    except litellm.RateLimitError as e:
        raise ValidationError(f"Rate limited by {provider}. Wait a moment and try again.") from e
    except litellm.APIConnectionError as e:
        raise ValidationError(f"Could not connect to {provider}. Check your internet connection.") from e
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e).strip()
        if not error_msg:
            raise ValidationError(
                f"Validation failed ({error_type}).\n"
                f"  Model: {model}\n"
                f"  Try a different model or check your {provider} dashboard."
            ) from e
        raise ValidationError(f"{error_type}: {error_msg}") from e


@app.command()
def init() -> None:
    """Initialize apitomcp with your LLM provider (first-time setup)."""
    from apitomcp.config import load_config, save_config
    from apitomcp.ui import (
        console,
        print_divider,
        print_error,
        print_header,
        print_info,
        print_success,
        prompt_text,
        spinner,
    )

    print_header("Welcome to apitomcp")

    console.print("This tool converts API documentation into MCP servers,")
    console.print("letting you chat with any API in Cursor.\n")

    # Check if already configured
    current_config = load_config()
    is_configured = bool(current_config.get("llm_api_key"))
    
    if is_configured:
        print_info(f"Already configured with {current_config.get('llm_provider', 'unknown')}.")
        print_info("Run 'apitomcp auth' to change your settings.\n")

    # Provider selection (with skip option if already configured)
    selected_provider = _select_provider(
        current_config.get("llm_provider"),
        allow_skip=is_configured,
    )
    provider = selected_provider if selected_provider else current_config.get("llm_provider")

    # API key handling
    if selected_provider is None:
        # User skipped provider selection, offer to keep existing key
        api_key = prompt_text(
            f"Enter {provider} API key (leave blank to keep current)",
            password=True,
        )
        if not api_key:
            api_key = current_config.get("llm_api_key", "")
    elif selected_provider == current_config.get("llm_provider"):
        # Same provider selected, offer to keep existing key
        api_key = prompt_text(
            f"Enter {provider} API key (leave blank to keep current)",
            password=True,
        )
        if not api_key:
            api_key = current_config.get("llm_api_key", "")
    else:
        # New provider, require new key
        api_key = prompt_text(f"Enter your {provider} API key", password=True)
        if not api_key:
            print_error("API key is required.")
            raise typer.Exit(1)

    # Model selection (with skip option if already configured and same provider)
    can_skip_model = is_configured and (selected_provider is None or selected_provider == current_config.get("llm_provider"))
    selected_model = _select_model(
        provider,
        current_config.get("llm_model"),
        allow_skip=can_skip_model,
    )
    model = selected_model if selected_model else current_config.get("llm_model")

    # Check if anything changed
    nothing_changed = (
        provider == current_config.get("llm_provider")
        and api_key == current_config.get("llm_api_key")
        and model == current_config.get("llm_model")
    )

    # Skip validation if nothing changed, otherwise validate and save
    if nothing_changed:
        print_success("Configuration unchanged!")
    else:
        with spinner(f"Validating {provider} credentials..."):
            try:
                _validate_api_key(provider, api_key, model)
            except ValidationError as e:
                print_error(str(e))
                raise typer.Exit(1)

        # Save config
        save_config({
            "llm_provider": provider,
            "llm_api_key": api_key,
            "llm_model": model,
        })

        print_success("Configuration saved!")
    print_divider()

    # Show next steps
    console.print("[bold]You're all set! Now you can generate an MCP server from API docs:[/bold]\n")
    console.print("[cyan]apitomcp generate[/cyan]\n")


@app.command()
def auth() -> None:
    """Update LLM provider, API key, or model."""
    from apitomcp.config import load_config, save_config
    from apitomcp.ui import (
        console,
        print_error,
        print_header,
        print_info,
        print_success,
        prompt_text,
        spinner,
    )

    print_header("Update LLM Settings")

    current_config = load_config()

    if not current_config.get("llm_api_key"):
        print_info("No configuration found. Running init instead...")
        init()
        return

    # Show current settings
    print_info(f"Current provider: {current_config.get('llm_provider', 'none')}")
    print_info(f"Current model: {current_config.get('llm_model', 'none')}")
    print_info("")

    # Provider selection
    provider = _select_provider(current_config.get("llm_provider"))

    # API key - show option to keep existing if same provider
    if provider == current_config.get("llm_provider"):
        api_key = prompt_text(
            f"Enter {provider} API key (leave blank to keep current)",
            password=True,
        )
        if not api_key:
            api_key = current_config.get("llm_api_key", "")
    else:
        api_key = prompt_text(f"Enter your {provider} API key", password=True)
        if not api_key:
            print_error("API key is required for new provider.")
            raise typer.Exit(1)

    # Model selection
    model = _select_model(provider, current_config.get("llm_model"))

    # Validate
    with spinner(f"Validating {provider} credentials..."):
        try:
            _validate_api_key(provider, api_key, model)
        except ValidationError as e:
            print_error(str(e))
            raise typer.Exit(1)

    # Save config
    save_config({
        "llm_provider": provider,
        "llm_api_key": api_key,
        "llm_model": model,
    })

    print_success(f"Updated! Using {provider} with {model}")


@app.command()
def generate() -> None:
    """Generate an MCP server from API documentation."""
    from apitomcp.config import load_config
    from apitomcp.ui import (
        console,
        print_error,
        print_header,
        print_info,
        print_success,
        print_warning,
        prompt_confirm,
        prompt_text,
        spinner,
    )

    print_header("Generate MCP Server")

    # Check LLM configuration
    config = load_config()
    if not config.get("llm_api_key"):
        print_error("No LLM provider configured. Run 'apitomcp init' first.")
        raise typer.Exit(1)

    # Get documentation URL
    url = prompt_text("Enter the API documentation URL")
    if not url:
        print_error("URL is required.")
        raise typer.Exit(1)

    # Validate URL format
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Import heavy modules now (after UI is shown)
    from apitomcp.generator import (
        ExtractionProgress,
        extract_operations_parallel,
        generate_openapi_spec,
        generate_openapi_spec_parallel,
        get_current_usage_stats,
        reset_usage_stats,
    )
    from apitomcp.scraper import ScrapeProgress, scrape_documentation
    from apitomcp.ui import LiveStatus
    from apitomcp.validator import validate_and_retry

    # Reset usage stats for this generation
    reset_usage_stats()

    # Phase 1: Scrape documentation with live progress
    try:
        with LiveStatus() as status:
            def on_scrape_progress(progress: ScrapeProgress) -> None:
                # Truncate URL for display
                short_url = progress.current_url
                if len(short_url) > 50:
                    short_url = "..." + short_url[-47:]
                status.update(
                    f"Scraping {progress.pages_scraped} pages | {short_url}"
                )

            scrape_result = scrape_documentation(url, on_progress=on_scrape_progress)
    except Exception as e:
        print_error(f"Failed to scrape documentation: {e}")
        raise typer.Exit(1)

    print_success(f"Scraped {scrape_result.pages_scraped} pages")

    # Detect and confirm base URL BEFORE extraction so LLM gets correct value
    from apitomcp.generator import detect_base_url_from_docs
    
    with spinner("Detecting API base URL..."):
        detected_base_url = _run_async(
            detect_base_url_from_docs(scrape_result.raw_markdown, url, config)
        )
    
    base_url = prompt_text("Enter the API base URL", default=detected_base_url)
    if not base_url:
        base_url = detected_base_url

    # Phase 2: LLM extracts operations from each page
    if not scrape_result.page_markdowns:
        print_error("No content found in scraped pages.")
        raise typer.Exit(1)

    print_info(f"Extracting API operations from {len(scrape_result.page_markdowns)} pages...")
    
    try:
        with LiveStatus() as status:
            def on_extraction_progress(progress: ExtractionProgress) -> None:
                short_url = progress.current_url
                if len(short_url) > 40:
                    short_url = "..." + short_url[-37:]
                status.update(
                    f"Extracting {progress.pages_processed}/{progress.total_pages} pages, "
                    f"{progress.operations_found} ops | {short_url}"
                )

            operations, extraction_stats = _run_async(
                extract_operations_parallel(
                    pages=scrape_result.page_markdowns,
                    config=config,
                    base_url=base_url,
                    on_progress=on_extraction_progress,
                )
            )
    except Exception as e:
        print_error(f"Failed to extract operations: {e}")
        raise typer.Exit(1)

    # Normalize operation paths to be relative to base URL
    from apitomcp.generator import normalize_operation_paths
    operations = normalize_operation_paths(operations, base_url)

    # Show extraction results
    if operations:
        print_success(f"Found {len(operations)} API operations")
        for op in operations[:5]:
            console.print(f"  [dim]• {op.method} {op.path}[/dim]")
        if len(operations) > 5:
            console.print(f"  [dim]... and {len(operations) - 5} more[/dim]")
        
        # Show extraction cost
        console.print(f"\n[dim]Extraction: {extraction_stats.format_summary()}[/dim]")
    else:
        print_warning("No API operations found in documentation")

    # Generate server name from URL
    from urllib.parse import urlparse

    parsed = urlparse(url)
    suggested_name = parsed.netloc.split(".")[0]
    if suggested_name in ("www", "api", "developer", "docs"):
        parts = parsed.netloc.split(".")
        suggested_name = parts[1] if len(parts) > 1 else parts[0]

    server_name = prompt_text("Enter a name for this server", default=suggested_name)
    if not server_name:
        print_error("Server name is required.")
        raise typer.Exit(1)

    # Sanitize server name
    server_name = server_name.lower().replace(" ", "_").replace("-", "_")

    # Phase 3: Generate OpenAPI spec from extracted operations
    print_info("Generating OpenAPI specification...")

    try:
        if operations and len(operations) >= 1:
            # Use parallel generation for operations
            print_info(f"Processing {len(operations)} operations...")

            # Run async generation
            openapi_spec, generation_stats = _run_async(
                generate_openapi_spec_parallel(
                    operations=operations,
                    config=config,
                    base_url=base_url,
                    api_title=server_name.title().replace("_", " ") + " API",
                )
            )
            # Combine stats from extraction and generation
            usage_stats = generation_stats
            usage_stats.total_tokens += extraction_stats.total_tokens
            usage_stats.prompt_tokens += extraction_stats.prompt_tokens
            usage_stats.completion_tokens += extraction_stats.completion_tokens
            usage_stats.total_cost += extraction_stats.total_cost
            usage_stats.calls += extraction_stats.calls
        else:
            # Fallback to single-call generation for docs with no extracted operations
            print_warning("No operations extracted, using full documentation...")
            openapi_spec = validate_and_retry(
                lambda: generate_openapi_spec(scrape_result.raw_markdown, config),
                config,
                max_retries=3,
            )
            usage_stats = get_current_usage_stats()
    except Exception as e:
        print_error(f"Failed to generate valid OpenAPI spec: {e}")
        raise typer.Exit(1)

    # Show usage stats and operation results
    if usage_stats:
        console.print("\n[bold]Generation complete![/bold]")
        console.print(usage_stats.format_summary())
        
        # Show base API URL
        console.print(f"  Base URL: {base_url}")
        console.print()
        
        # Show detailed operation results in a table
        if usage_stats.operation_results:
            from apitomcp.ui import create_table, print_table
            
            table = create_table(
                f"Operation Results ({usage_stats.successful_ops} success, {usage_stats.failed_ops} failed)",
                ["Status", "Method", "Path", "Tool ID / Error"]
            )
            
            # Sort: successful first, then failed
            sorted_results = sorted(
                usage_stats.operation_results,
                key=lambda r: (0 if r.status == "success" else 1, r.path)
            )
            
            for result in sorted_results:
                if result.status == "success":
                    status_str = "[green]✓[/green]"
                    detail = result.operation_id or result.summary or "-"
                else:
                    status_str = "[red]✗[/red]"
                    detail = f"[red]{result.error}[/red]" if result.error else "[red]Failed[/red]"
                
                table.add_row(
                    status_str,
                    result.method,
                    result.path,
                    detail,
                )
            
            print_table(table)
        else:
            # For single-call generation, extract tools from the spec and display
            from apitomcp.ui import create_table, print_table
            
            paths = openapi_spec.get("paths", {})
            if paths:
                tool_count = sum(
                    1 for path_item in paths.values()
                    for method in ["get", "post", "put", "patch", "delete", "head", "options"]
                    if method in path_item
                )
                table = create_table(
                    f"Generated Tools ({tool_count})",
                    ["Method", "Path", "Tool ID", "Summary"]
                )
                
                for path, path_item in sorted(paths.items()):
                    for method in ["get", "post", "put", "patch", "delete", "head", "options"]:
                        if method in path_item:
                            op = path_item[method]
                            table.add_row(
                                method.upper(),
                                path,
                                op.get("operationId", "-"),
                                op.get("summary", "-")[:50],
                            )
                
                print_table(table)

    # Detect authentication using LLM analysis
    from apitomcp.generator import (
        ApiKeyConfig,
        BearerConfig,
        DetectedAuth,
        OAuth2AuthCodeConfig,
        OAuth2ClientCredsConfig,
        detect_auth_from_docs,
    )
    from apitomcp.ui import prompt_choice

    # Use LLM to analyze auth documentation
    auth_config: dict = {"type": "none"}
    detected_auth: DetectedAuth | None = None

    if scrape_result.auth_content:
        print_info("Analyzing authentication requirements...")
        try:
            detected_auth = _run_async(detect_auth_from_docs(scrape_result.auth_content, config))
        except Exception as e:
            print_warning(f"Auth detection failed: {e}")
            detected_auth = None

    # Build display based on detected auth
    if detected_auth and detected_auth.type != "none":
        # Map types to friendly names
        auth_type_names = {
            "oauth2_client_credentials": "OAuth2 Client Credentials",
            "oauth2_auth_code": "OAuth2 Authorization Code",
            "bearer": "Bearer Token",
            "api_key": "API Key",
            "none": "None",
        }

        detected_name = auth_type_names.get(detected_auth.type, detected_auth.type)
        confidence_color = {"high": "green", "medium": "yellow", "low": "red"}.get(
            detected_auth.confidence, "white"
        )

        console.print(f"\n[bold]Detected: {detected_name}[/bold] [{confidence_color}]({detected_auth.confidence} confidence)[/{confidence_color}]")

        # Show details based on auth type
        cfg = detected_auth.config
        if isinstance(cfg, OAuth2ClientCredsConfig):
            console.print(f"  Token URL: [cyan]{cfg.token_url}[/cyan]")
            if cfg.scopes:
                console.print(f"  Scopes: {', '.join(cfg.scopes)}")
            if cfg.notes:
                console.print(f"  [muted]{cfg.notes}[/muted]")
        elif isinstance(cfg, OAuth2AuthCodeConfig):
            console.print(f"  Token URL: [cyan]{cfg.token_url}[/cyan]")
            console.print(f"  Auth URL: [cyan]{cfg.auth_url}[/cyan]")
            if cfg.scopes:
                console.print(f"  Scopes: {', '.join(cfg.scopes)}")
            if cfg.notes:
                console.print(f"  [muted]{cfg.notes}[/muted]")
        elif isinstance(cfg, BearerConfig):
            if cfg.notes:
                console.print(f"  [muted]{cfg.notes}[/muted]")
        elif isinstance(cfg, ApiKeyConfig):
            console.print(f"  Header: [cyan]{cfg.header_name}[/cyan]")
            if cfg.header_prefix:
                console.print(f"  Prefix: {cfg.header_prefix}")
            if cfg.notes:
                console.print(f"  [muted]{cfg.notes}[/muted]")

        console.print()
        use_detected = prompt_confirm("Use this authentication type?")
    else:
        print_info("No authentication detected from documentation.")
        use_detected = False

    # If not using detected type, let user choose
    chosen_type = detected_auth.type if detected_auth and use_detected else None

    if not use_detected:
        auth_choices = [
            "OAuth2 Client Credentials (client_id + secret)",
            "Bearer Token",
            "API Key",
            "None",
        ]

        auth_choice = prompt_choice("Select authentication type", auth_choices)

        if "OAuth2" in auth_choice:
            chosen_type = "oauth2_client_credentials"
        elif "Bearer" in auth_choice:
            chosen_type = "bearer"
        elif "API Key" in auth_choice:
            chosen_type = "api_key"
        else:
            chosen_type = "none"

    # Now configure based on the chosen type
    if chosen_type == "oauth2_client_credentials":
        auth_config["type"] = "oauth2_client_credentials"
        auth_config["header_name"] = "Authorization"
        auth_config["value_prefix"] = "Bearer "

        console.print("\n[muted]OAuth2 will automatically refresh tokens using your client credentials.[/muted]\n")

        # Pre-fill token_url if detected
        default_token_url = ""
        if detected_auth and isinstance(detected_auth.config, OAuth2ClientCredsConfig):
            default_token_url = detected_auth.config.token_url

        token_url = prompt_text("Enter the token URL", default=default_token_url)
        if token_url:
            auth_config["token_url"] = token_url

        client_id = prompt_text("Enter your Client ID", password=True)
        if client_id:
            auth_config["client_id"] = client_id

        client_secret = prompt_text("Enter your Client Secret", password=True)
        if client_secret:
            auth_config["client_secret"] = client_secret

        console.print("\n[muted]Server will automatically refresh access tokens using the token URL.[/muted]")

    elif chosen_type == "bearer":
        auth_config["type"] = "bearer"
        auth_config["header_name"] = "Authorization"
        auth_config["value_prefix"] = "Bearer "

        token = prompt_text(f"Enter your {server_name} bearer token", password=True)
        if token:
            auth_config["value"] = token

    elif chosen_type == "api_key":
        auth_config["type"] = "api_key"

        # Pre-fill header name if detected
        default_header = "X-API-Key"
        if detected_auth and isinstance(detected_auth.config, ApiKeyConfig):
            default_header = detected_auth.config.header_name

        header_name = prompt_text("Enter the header name", default=default_header)
        auth_config["header_name"] = header_name
        auth_config["value_prefix"] = ""

        api_key = prompt_text(f"Enter your {server_name} API key", password=True)
        if api_key:
            auth_config["value"] = api_key

    else:
        auth_config["type"] = "none"

    # Save the server configuration
    from datetime import datetime, timezone

    from apitomcp.config import save_openapi_spec, save_server_config

    tool_count = count_tools(openapi_spec)

    server_config = {
        "server_name": server_name,
        "source_url": url,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "auth": auth_config,
        "tool_count": tool_count,
        "tool_overrides": {},
    }

    save_server_config(server_name, server_config)
    save_openapi_spec(server_name, openapi_spec)

    print_success(f"Server '{server_name}' created with {tool_count} tools!")
    
    # Smart installation detection and prompt for Cursor and Claude Desktop
    from InquirerPy.base.control import Choice

    from apitomcp.installer import (
        detect_available_targets,
        install_to_target,
        is_installed_in_target,
    )
    from apitomcp.ui import prompt_confirm, prompt_select_multiple
    
    targets = detect_available_targets()
    if not targets:
        print_info("No MCP clients detected. Run 'apitomcp install' after installing Cursor or Claude Desktop.")
    else:
        # Check which targets already have this server installed
        already_installed = [t for t in targets if is_installed_in_target(server_name, t.config_path)]
        not_installed = [t for t in targets if not is_installed_in_target(server_name, t.config_path)]
        
        # Notify about updates to existing installations
        for target in already_installed:
            target_label = "Cursor" if target.name == "cursor" else "Claude Desktop"
            print_info(f"Server '{server_name}' updated in {target_label}. Restart to apply changes.")
        
        # Offer to install to new targets
        if not_installed:
            console.print()
            if len(not_installed) == 1:
                target = not_installed[0]
                target_label = "Cursor" if target.name == "cursor" else "Claude Desktop"
                print_info(f"{target_label} detected at {target.config_path}")
                if prompt_confirm(f"Install '{server_name}' to {target_label}?", default=True):
                    try:
                        install_to_target(server_name, target.config_path)
                        print_success(f"Installed '{server_name}' to {target_label}!")
                        print_info(f"Restart {target_label} to start using this MCP server.")
                    except Exception as e:
                        print_error(f"Failed to install: {e}")
                        print_info("You can manually run 'apitomcp install' later.")
                else:
                    print_info("Run 'apitomcp install' to add it later.")
            else:
                # Multiple targets available - let user select
                print_info("Multiple MCP clients detected:")
                for target in not_installed:
                    print_info(f"  • {target.display_name}")
                
                choices = [
                    Choice(value=target.name, name=target.display_name)
                    for target in not_installed
                ]
                
                console.print("[muted]Use space to toggle, enter to confirm[/muted]")
                selected_names = prompt_select_multiple(
                    f"Install '{server_name}' to",
                    choices,
                )
                
                if selected_names:
                    restart_targets = []
                    for target in not_installed:
                        if target.name in selected_names:
                            try:
                                install_to_target(server_name, target.config_path)
                                target_label = "Cursor" if target.name == "cursor" else "Claude Desktop"
                                print_success(f"Installed '{server_name}' to {target_label}!")
                                restart_targets.append(target_label)
                            except Exception as e:
                                print_error(f"Failed to install to {target.name}: {e}")
                    
                    if restart_targets:
                        print_info(f"Restart {' and '.join(restart_targets)} to start using this MCP server.")
                else:
                    print_info("Run 'apitomcp install' to add it later.")


def count_tools(spec: dict) -> int:
    """Count the number of operations/tools in an OpenAPI spec."""
    count = 0
    for path_item in spec.get("paths", {}).values():
        for method in ["get", "post", "put", "patch", "delete", "head", "options"]:
            if method in path_item:
                count += 1
    return count


@app.command("list")
def list_servers() -> None:
    """List all generated MCP servers."""
    from datetime import datetime

    from apitomcp.config import list_servers as get_servers
    from apitomcp.config import load_server_config
    from apitomcp.ui import create_table, print_info, print_table

    servers = get_servers()

    if not servers:
        print_info("No servers generated yet. Run 'apitomcp generate' to create one.")
        return

    table = create_table("Generated Servers", ["Name", "Source URL", "Created", "Tools"])

    for server_name in sorted(servers):
        config = load_server_config(server_name)
        if config:
            # Format created date
            created_at = config.get("created_at", "")
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    created_str = format_relative_time(dt)
                except ValueError:
                    created_str = created_at[:10]
            else:
                created_str = "Unknown"

            # Truncate URL if too long
            source_url = config.get("source_url", "")
            if len(source_url) > 50:
                source_url = source_url[:47] + "..."

            table.add_row(
                server_name,
                source_url,
                created_str,
                str(config.get("tool_count", 0)),
            )

    print_table(table)


def format_relative_time(dt: datetime.datetime) -> str:
    """Format a datetime as a relative time string."""
    now = datetime.datetime.now(datetime.timezone.utc)
    diff = now - dt

    if diff.days == 0:
        if diff.seconds < 60:
            return "just now"
        elif diff.seconds < 3600:
            minutes = diff.seconds // 60
            return f"{minutes}m ago"
        else:
            hours = diff.seconds // 3600
            return f"{hours}h ago"
    elif diff.days == 1:
        return "yesterday"
    elif diff.days < 7:
        return f"{diff.days}d ago"
    elif diff.days < 30:
        weeks = diff.days // 7
        return f"{weeks}w ago"
    else:
        return dt.strftime("%Y-%m-%d")


@app.command()
def delete() -> None:
    """Delete a generated MCP server."""
    from apitomcp.config import delete_server
    from apitomcp.config import list_servers as get_servers
    from apitomcp.ui import (
        print_error,
        print_header,
        print_info,
        print_success,
        prompt_choice,
        prompt_confirm,
    )

    print_header("Delete Server")

    servers = get_servers()
    if not servers:
        print_info("No servers to delete.")
        return

    server_name = prompt_choice("Select a server to delete", sorted(servers))

    if not prompt_confirm(f"Are you sure you want to delete '{server_name}'?"):
        print_info("Cancelled.")
        return

    if delete_server(server_name):
        print_success(f"Deleted '{server_name}'")
    else:
        print_error(f"Failed to delete '{server_name}'")


@app.command()
def install() -> None:
    """Install generated servers to Cursor and/or Claude Desktop."""
    from InquirerPy.base.control import Choice

    from apitomcp.config import list_servers as get_servers
    from apitomcp.installer import detect_available_targets, install_to_target, is_installed_in_target
    from apitomcp.ui import (
        console,
        print_error,
        print_header,
        print_info,
        print_success,
        print_warning,
        prompt_select_multiple,
    )

    print_header("Install MCP Servers")

    servers = get_servers()
    if not servers:
        print_error("No servers generated yet. Run 'apitomcp generate' first.")
        raise typer.Exit(1)

    # Detect available targets
    targets = detect_available_targets()
    if not targets:
        print_error("No MCP clients found.")
        print_info("Make sure Cursor or Claude Desktop is installed and has been run at least once.")
        raise typer.Exit(1)

    print_info(f"Found servers: {', '.join(sorted(servers))}")

    # Check which targets already have any of the servers installed
    def has_any_server_installed(target):
        return any(is_installed_in_target(s, target.config_path) for s in servers)

    # Multi-select for installation targets, pre-select targets that already have servers
    choices = [
        Choice(
            value=target.name,
            name=target.display_name,
            enabled=has_any_server_installed(target),
        )
        for target in targets
    ]

    console.print("[muted]Use space to toggle, enter to confirm[/muted]")
    selected_names = prompt_select_multiple(
        "Select installation targets",
        choices,
    )

    if not selected_names:
        print_info("No targets selected. Cancelled.")
        raise typer.Exit(0)

    selected_targets = [t for t in targets if t.name in selected_names]

    # Install each server to each target
    target_names = []
    for target in selected_targets:
        for server_name in sorted(servers):
            try:
                install_to_target(server_name, target.config_path)
                target_label = "Cursor" if target.name == "cursor" else "Claude Desktop"
                print_success(f"Installed '{server_name}' to {target_label}")
            except Exception as e:
                print_error(f"Failed to install '{server_name}' to {target.name}: {e}")

        if target.name == "cursor":
            target_names.append("Cursor")
        else:
            target_names.append("Claude Desktop")

    if target_names:
        print_warning(f"Please restart {' and '.join(target_names)} for changes to take effect.")


@app.command()
def output() -> None:
    """Export server files to current directory."""
    import shutil
    from pathlib import Path

    from apitomcp.config import get_servers_dir
    from apitomcp.config import list_servers as get_servers
    from apitomcp.ui import (
        print_error,
        print_header,
        print_info,
        print_success,
        prompt_choice,
    )

    print_header("Export Server Files")

    servers = get_servers()
    if not servers:
        print_error("No servers generated yet. Run 'apitomcp generate' first.")
        raise typer.Exit(1)

    server_name = prompt_choice("Select a server to export", sorted(servers))

    # Get source and destination paths
    server_dir = get_servers_dir() / server_name
    dest_dir = Path.cwd() / server_name

    if dest_dir.exists():
        print_error(f"Directory '{server_name}' already exists in current directory.")
        raise typer.Exit(1)

    # Copy files
    shutil.copytree(server_dir, dest_dir)

    print_success(f"Exported to ./{server_name}/")
    print_info("Files exported:")
    for file in dest_dir.iterdir():
        print_info(f"  - {file.name}")


@app.command()
def run(server_name: str = typer.Argument(..., help="Name of the server to run")) -> None:
    """Run an MCP server (used by MCP clients)."""
    from apitomcp.config import load_openapi_spec, load_server_config
    from apitomcp.runner import create_and_run_server
    from apitomcp.ui import print_error

    # Load server configuration
    config = load_server_config(server_name)
    if not config:
        print_error(f"Server '{server_name}' not found.")
        raise typer.Exit(1)

    spec = load_openapi_spec(server_name)
    if not spec:
        print_error(f"OpenAPI spec for '{server_name}' not found.")
        raise typer.Exit(1)

    # Run the server
    create_and_run_server(server_name, config, spec)


if __name__ == "__main__":
    app()
