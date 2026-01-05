"""OpenAPI specification generation using LLMs with parallel processing."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from apitomcp.config import LLMConfig
    from apitomcp.scraper import Operation, PageMarkdown


@dataclass
class OperationResult:
    """Result of generating a single operation."""
    
    method: str
    path: str
    status: str  # "success", "failed", "error"
    operation_id: str = ""
    summary: str = ""
    error: str = ""


@dataclass
class UsageStats:
    """Track LLM token usage and costs."""

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0
    calls: int = 0
    model: str = ""
    cost_available: bool = True
    operation_results: list[OperationResult] = field(default_factory=list)
    
    @property
    def successful_ops(self) -> int:
        return sum(1 for r in self.operation_results if r.status == "success")
    
    @property
    def failed_ops(self) -> int:
        return sum(1 for r in self.operation_results if r.status != "success")

    def add_response(self, response: dict, model: str) -> None:
        """Add usage from a completion response."""
        import litellm

        self.model = model
        self.calls += 1

        usage = getattr(response, "usage", None)
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0

        # Try to calculate cost
        if self.cost_available:
            try:
                cost = litellm.completion_cost(completion_response=response)
                self.total_cost += cost
            except Exception:
                self.cost_available = False

    def format_summary(self) -> str:
        """Format usage stats for display."""
        lines = [
            f"  LLM Calls: {self.calls}",
            f"  Tokens: {self.total_tokens:,} (prompt: {self.prompt_tokens:,} / completion: {self.completion_tokens:,})",
        ]

        if self.cost_available and self.total_cost > 0:
            lines.append(f"  Estimated Cost: ${self.total_cost:.4f}")
        elif not self.cost_available:
            lines.append(f"  Cost estimate not available for model: {self.model}")

        return "\n".join(lines)


# Global usage stats for current generation
_current_stats: UsageStats | None = None


# ============================================================================
# Authentication Detection Types
# ============================================================================


@dataclass
class OAuth2ClientCredsConfig:
    """OAuth2 Client Credentials flow - use client_id + secret to get tokens."""

    token_url: str  # Required: e.g., https://accounts.spotify.com/api/token
    scopes: list[str] | None = None
    notes: str = ""  # Where to get client_id/secret


@dataclass
class OAuth2AuthCodeConfig:
    """OAuth2 Authorization Code flow - user login redirect."""

    token_url: str  # Required: endpoint to exchange code for token
    auth_url: str  # Required: where to redirect user for login
    scopes: list[str] | None = None
    notes: str = ""


@dataclass
class BearerConfig:
    """Static bearer token."""

    notes: str = ""  # Where to get the token


@dataclass
class ApiKeyConfig:
    """API Key in header."""

    header_name: str  # Required: "X-API-Key", "Authorization", etc.
    header_prefix: str = ""  # Optional: "Bearer ", "Api-Key "
    notes: str = ""  # Where to get the key


# Union type for type-specific config
AuthConfigUnion = OAuth2ClientCredsConfig | OAuth2AuthCodeConfig | BearerConfig | ApiKeyConfig | None


@dataclass
class DetectedAuth:
    """Result of LLM-based authentication detection."""

    type: str  # "oauth2_client_credentials", "oauth2_auth_code", "bearer", "api_key", "none"
    confidence: str  # "high", "medium", "low"
    config: AuthConfigUnion  # Type-specific configuration (or None for "none" type)


AUTH_DETECTION_PROMPT = """You are an API authentication expert. Analyze the provided documentation and identify the authentication method.

IMPORTANT: Extract EXACT URLs, header names, and other details from the documentation. Do not guess or make up values.

Identify ONE of these authentication types:
1. oauth2_client_credentials - Uses client_id + client_secret to get access tokens from a token endpoint
2. oauth2_auth_code - OAuth2 with user login/redirect flow (authorization code)
3. bearer - Static bearer token (user provides token directly)
4. api_key - API key sent in a header
5. none - No authentication required

Return a JSON object with this structure:
{
  "type": "oauth2_client_credentials" | "oauth2_auth_code" | "bearer" | "api_key" | "none",
  "confidence": "high" | "medium" | "low",
  "config": { ... type-specific fields ... }
}

Type-specific config fields:
- oauth2_client_credentials: {"token_url": "...", "scopes": [...], "notes": "..."}
- oauth2_auth_code: {"token_url": "...", "auth_url": "...", "scopes": [...], "notes": "..."}
- bearer: {"notes": "..."}
- api_key: {"header_name": "...", "header_prefix": "...", "notes": "..."}
- none: null

Confidence levels:
- high: Clear documentation with exact URLs/details
- medium: Auth type is clear but some details are missing
- low: Inferred from context, may be incorrect

Return ONLY the JSON object, no other text."""


async def detect_auth_from_docs(
    auth_content: str, config: "LLMConfig"
) -> DetectedAuth:
    """
    Use LLM to analyze auth documentation and extract configuration.

    Args:
        auth_content: Extracted authentication documentation
        config: LLM configuration

    Returns:
        DetectedAuth with type, confidence, and type-specific config
    """
    import litellm

    litellm.suppress_debug_info = True
    _set_api_key_env(config)

    model = config.get("llm_model")
    if not model:
        raise ValueError("LLM model not configured. Run 'apitomcp init' or 'apitomcp auth' to set up.")

    # If no auth content, return none
    if not auth_content or len(auth_content.strip()) < 50:
        return DetectedAuth(type="none", confidence="low", config=None)

    # Truncate if too long
    max_length = 15000
    if len(auth_content) > max_length:
        auth_content = auth_content[:max_length] + "\n[truncated]"

    user_prompt = f"""Analyze this API authentication documentation and identify the authentication method.

DOCUMENTATION:
{auth_content}

Return ONLY a JSON object with the authentication type, confidence level, and configuration details."""

    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": AUTH_DETECTION_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1000,
            temperature=0.1,
        )

        # Track usage
        global _current_stats
        if _current_stats:
            _current_stats.add_response(response, model)

        content = response.choices[0].message.content
        result = extract_json(content)

        auth_type = result.get("type", "none")
        confidence = result.get("confidence", "low")
        config_data = result.get("config")

        # Parse type-specific config
        parsed_config: AuthConfigUnion = None

        if auth_type == "oauth2_client_credentials" and config_data:
            token_url = config_data.get("token_url", "")
            if token_url:
                parsed_config = OAuth2ClientCredsConfig(
                    token_url=token_url,
                    scopes=config_data.get("scopes"),
                    notes=config_data.get("notes", ""),
                )
            else:
                # Missing required field, downgrade confidence
                confidence = "low"

        elif auth_type == "oauth2_auth_code" and config_data:
            token_url = config_data.get("token_url", "")
            auth_url = config_data.get("auth_url", "")
            if token_url and auth_url:
                parsed_config = OAuth2AuthCodeConfig(
                    token_url=token_url,
                    auth_url=auth_url,
                    scopes=config_data.get("scopes"),
                    notes=config_data.get("notes", ""),
                )
            else:
                confidence = "low"

        elif auth_type == "bearer" and config_data:
            parsed_config = BearerConfig(
                notes=config_data.get("notes", ""),
            )

        elif auth_type == "api_key" and config_data:
            header_name = config_data.get("header_name", "")
            if header_name:
                parsed_config = ApiKeyConfig(
                    header_name=header_name,
                    header_prefix=config_data.get("header_prefix", ""),
                    notes=config_data.get("notes", ""),
                )
            else:
                confidence = "low"

        return DetectedAuth(
            type=auth_type,
            confidence=confidence,
            config=parsed_config,
        )

    except Exception as e:
        # On error, return none with low confidence
        return DetectedAuth(
            type="none",
            confidence="low",
            config=None,
        )


async def detect_base_url_from_docs(
    documentation: str, source_url: str, config: "LLMConfig"
) -> str:
    """
    Use LLM to analyze documentation and extract the API base URL.

    Args:
        documentation: Scraped documentation content
        source_url: The URL the documentation was scraped from
        config: LLM configuration

    Returns:
        Detected base URL string
    """
    import litellm
    from urllib.parse import urlparse

    litellm.suppress_debug_info = True
    _set_api_key_env(config)

    model = config.get("llm_model")
    if not model:
        # Fallback to source URL domain
        parsed = urlparse(source_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    # Truncate documentation if too long
    max_length = 10000
    doc_sample = documentation[:max_length] if len(documentation) > max_length else documentation

    user_prompt = f"""Analyze this API documentation and determine the base URL for API requests.

SOURCE URL (where documentation was found):
{source_url}

DOCUMENTATION CONTENT:
{doc_sample}

Look for:
- Curl examples with full URLs
- API endpoint examples
- Base URL mentioned in text
- Server URLs

Return ONLY a JSON object with this format:
{{"base_url": "https://api.example.com", "confidence": "high"}}

confidence levels:
- high: Found explicit base URL in curl examples or documentation
- medium: Inferred from endpoint patterns
- low: Guessed from domain

Return ONLY the JSON, no other text."""

    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": "You extract API base URLs from documentation. Return only valid JSON."},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=200,
            temperature=0.1,
        )

        # Track usage
        global _current_stats
        if _current_stats:
            _current_stats.add_response(response, model)

        content = response.choices[0].message.content
        result = extract_json(content)
        
        base_url = result.get("base_url", "")
        if base_url and base_url.startswith("http"):
            # Clean up trailing slashes
            return base_url.rstrip("/")

    except Exception:
        pass

    # Fallback to source URL domain
    parsed = urlparse(source_url)
    return f"{parsed.scheme}://{parsed.netloc}"


# ============================================================================
# LLM-Based Operation Extraction
# ============================================================================

EXTRACTION_SYSTEM_PROMPT = """You are an expert API documentation analyzer.

Your task is to identify API operations (endpoints) documented on a page and extract structured information.

CRITICAL RULES:
1. ONLY extract operations with paths EXACTLY as documented - do not invent variations
2. DO NOT create singular/plural variants (if docs show /albums/{id}, do not also create /album/{id})
3. SKIP paths that contain hardcoded IDs or example values (e.g., /tracks/2TpxZ7JUBn3uw46aR7qd6V)
4. SKIP paths that look like examples rather than endpoint definitions
5. If a path appears in a curl example, extract the PATTERN not the literal URL
   - curl example: GET /albums/4aawyAB9vmqN3uQ7FjRGTy -> extract as /albums/{id}
6. Each operation must have: HTTP method and a parameterized path template
7. If an operation looks wrong, malformed, or like garbage data - DO NOT include it
8. When in doubt, OMIT rather than guess
9. DO NOT infer HTTP methods from endpoint names - extract the EXACT method documented
   - /delete-entitlements might use POST, not DELETE
   - /get-entitlements might use POST, not GET
   - Only use the method explicitly shown in the documentation
10. SKIP deprecated endpoints - if an endpoint is marked as "Deprecated" or "deprecated", do NOT include it
    - Look for "Deprecated" badges, labels, or text near the endpoint
    - Do NOT skip endpoints that say "will be deprecated" in the future - only skip currently deprecated ones

PATH VALIDATION:
- Valid: /users/{id}, /albums/{album_id}/tracks, /me/player
- Invalid: /albums/4aawyAB9vmqN3uQ7FjRGTy (hardcoded ID)

BASE URL PATH STRIPPING:
- If base URL ends with /v1, /v2, /3, etc., strip that prefix from paths
- Example: base URL "https://api.example.com/v1" + doc shows "/v1/users" -> return "/users"
- The returned path should be RELATIVE to the base URL, not include the version prefix"""

EXTRACTION_USER_PROMPT = """Analyze this API documentation page and extract API operations (endpoints).

SOURCE URL: {source_url}
API BASE URL: {base_url}

DOCUMENTATION CONTENT:
{content}

IMPORTANT:
- STRIP the base URL path prefix from all extracted paths:
  * If base URL is https://api.demo.com/v1, then /v1/albums -> /albums
  * The path you return should NOT include a version prefix that's already in the base URL
- Only extract operations that are CLEARLY DEFINED as endpoints, not example URLs
- If a path contains what looks like a hardcoded ID (alphanumeric strings like "4aawyAB9vmqN3uQ7FjRGTy"), SKIP it entirely
- Do NOT invent endpoint variations - extract EXACTLY what is documented
- Do NOT infer HTTP methods from endpoint names (e.g., /delete-* might use POST not DELETE) - use ONLY the method shown in docs
- SKIP any endpoint marked as "Deprecated" - do not include deprecated APIs

For each valid API operation found, extract:
- method: HTTP method (GET, POST, PUT, PATCH, DELETE)
- path: The endpoint path template (e.g., /users/{{id}}, /albums)
- summary: Brief description (1 line)
- description: Longer description if available
- parameters: Path/query parameters with name, type, required, description
- request_body: Request body schema if documented

Return JSON:
{{
  "operations": [
    {{
      "method": "GET",
      "path": "/albums/{{id}}",
      "summary": "Get an album",
      "parameters": [
        {{"name": "id", "in": "path", "type": "string", "required": true, "description": "Spotify album ID"}}
      ]
    }}
  ]
}}

If NO valid API operations are found, or all candidates look invalid, return:
{{"operations": []}}

Return ONLY the JSON, no other text."""


async def extract_operations_from_page(
    markdown: str,
    source_url: str,
    config: "LLMConfig",
    stats: "UsageStats",
    base_url: str = "",
) -> list["Operation"]:
    """
    Use LLM to extract API operations from a documentation page.

    Args:
        markdown: The page content as markdown
        source_url: The URL the page was scraped from
        config: LLM configuration
        stats: Usage stats to update
        base_url: API base URL for path normalization

    Returns:
        List of Operation objects found on the page
    """
    import litellm
    from apitomcp.scraper import Operation

    litellm.suppress_debug_info = True
    _set_api_key_env(config)

    model = config.get("llm_model")
    if not model:
        return []

    # Skip very short pages
    if not markdown or len(markdown.strip()) < 100:
        return []

    # Truncate if too long
    max_length = 12000
    if len(markdown) > max_length:
        markdown = markdown[:max_length] + "\n[truncated]"

    user_prompt = EXTRACTION_USER_PROMPT.format(
        source_url=source_url,
        base_url=base_url or "unknown",
        content=markdown,
    )

    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=2000,
            temperature=0.1,
        )

        # Track usage
        stats.add_response(response, model)

        content = response.choices[0].message.content
        result = extract_json(content)

        operations: list[Operation] = []
        for op_data in result.get("operations", []):
            method = op_data.get("method", "").upper()
            path = op_data.get("path", "")

            # Validate required fields
            if not method or not path:
                continue

            # Ensure path starts with /
            if not path.startswith("/"):
                continue

            # Build parameters text from extracted params
            params_text = ""
            if op_data.get("parameters"):
                params_lines = []
                for p in op_data["parameters"]:
                    req = "required" if p.get("required") else "optional"
                    params_lines.append(f"- {p.get('name')}: {p.get('type', 'string')} ({req}) - {p.get('description', '')}")
                params_text = "\n".join(params_lines)

            if op_data.get("request_body"):
                params_text += f"\n\nRequest Body:\n{json.dumps(op_data['request_body'], indent=2)}"

            operations.append(Operation(
                method=method,
                path=path,
                summary=op_data.get("summary", ""),
                description=op_data.get("description", ""),
                parameters_text=params_text,
                source_url=source_url,
            ))

        return operations

    except Exception:
        return []


@dataclass
class ExtractionProgress:
    """Progress update during operation extraction."""
    pages_processed: int
    total_pages: int
    operations_found: int
    current_url: str


async def extract_operations_parallel(
    pages: list[PageMarkdown],
    config: "LLMConfig",
    base_url: str = "",
    on_progress: "Callable[[ExtractionProgress], None] | None" = None,
) -> tuple[list["Operation"], "UsageStats"]:
    """
    Extract operations from all pages using parallel LLM calls.

    Args:
        pages: List of PageMarkdown objects with url and markdown content
        config: LLM configuration
        base_url: API base URL for path normalization
        on_progress: Optional callback for progress updates

    Returns:
        Tuple of (list of all operations found, usage stats)
    """
    from apitomcp.scraper import Operation, PageMarkdown

    stats = UsageStats()
    global _current_stats
    _current_stats = stats

    all_operations: list[Operation] = []
    seen_ops: set[tuple[str, str]] = set()  # For deduplication

    # Process pages concurrently with semaphore to limit parallelism
    semaphore = asyncio.Semaphore(10)  # Limit concurrent LLM calls
    processed_count = 0

    async def process_page(page: PageMarkdown) -> list[Operation]:
        nonlocal processed_count
        async with semaphore:
            ops = await extract_operations_from_page(
                markdown=page.markdown,
                source_url=page.url,
                config=config,
                stats=stats,
                base_url=base_url,
            )
            processed_count += 1
            return ops

    # Create tasks for all pages
    tasks = [process_page(page) for page in pages]

    # Process with progress updates
    for coro in asyncio.as_completed(tasks):
        page_ops = await coro
        
        # Deduplicate and add operations
        for op in page_ops:
            key = (op.method, op.path)
            if key not in seen_ops:
                seen_ops.add(key)
                all_operations.append(op)

        # Report progress
        if on_progress:
            # Find current URL (approximate since as_completed doesn't preserve order)
            current_url = pages[min(processed_count - 1, len(pages) - 1)].url if pages else ""
            on_progress(ExtractionProgress(
                pages_processed=processed_count,
                total_pages=len(pages),
                operations_found=len(all_operations),
                current_url=current_url,
            ))

    # Sort operations by path for consistent ordering
    all_operations.sort(key=lambda op: (op.path, op.method))

    return all_operations, stats


def normalize_operation_paths(operations: list["Operation"], base_url: str) -> list["Operation"]:
    """
    Strip base URL path prefix from operation paths.
    
    For example, if base_url is "https://api.themoviedb.org/3",
    then "/3/account/{id}" becomes "/account/{id}".
    
    Args:
        operations: List of Operation objects to normalize
        base_url: The API base URL containing the path prefix to strip
        
    Returns:
        The same list with paths normalized (modified in place)
    """
    from urllib.parse import urlparse
    
    base_path = urlparse(base_url).path.rstrip("/")
    if not base_path:
        return operations
    
    for op in operations:
        if op.path.startswith(base_path):
            op.path = op.path[len(base_path):] or "/"
    
    return operations


# ============================================================================
# OpenAPI Specification Generation
# ============================================================================

SYSTEM_PROMPT = """You are an expert API documentation analyzer and OpenAPI specification generator.

Your task is to analyze the provided API operation documentation and generate a valid OpenAPI 3.1.0 path item in JSON format.

CRITICAL RULES:
1. ONLY include information that is explicitly documented in the provided content
2. DO NOT invent or hallucinate any parameters, schemas, or response fields
3. If information is unclear or missing, omit it rather than guessing
4. All paths must be valid URL paths starting with /
5. Use the exact HTTP method provided (lowercase: get, post, put, patch, delete)
6. Extract parameter types from examples when available
7. Use descriptive operationIds in snake_case format

OUTPUT FORMAT:
- Return ONLY valid JSON, no markdown code blocks
- The JSON should be a path item object for the specific operation
- Do not include any explanatory text before or after the JSON"""

OPERATION_PROMPT_TEMPLATE = """Analyze this API operation and generate an OpenAPI 3.1.0 path item object.

HTTP Method: {method}
Path: {path}

DOCUMENTATION:
{documentation}

{examples_section}

Generate a JSON object with this structure:
{{
  "{method_lower}": {{
    "operationId": "snake_case_operation_name",
    "summary": "Brief summary",
    "description": "Detailed description if available",
    "parameters": [...],  // path, query, header parameters
    "requestBody": {{}},  // if POST/PUT/PATCH with body
    "responses": {{
      "200": {{"description": "Success response"}}
    }}
  }}
}}

Return ONLY the JSON object, no other text."""


def _set_api_key_env(config: "LLMConfig") -> None:
    """Set the appropriate environment variable for the LLM provider."""
    import os

    provider = config.get("llm_provider", "")
    api_key = config.get("llm_api_key", "")

    if not api_key:
        return

    env_vars = {
        "OpenRouter": "OPENROUTER_API_KEY",
        "Anthropic": "ANTHROPIC_API_KEY",
        "OpenAI": "OPENAI_API_KEY",
        "Gemini": "GEMINI_API_KEY",
    }

    if provider in env_vars:
        os.environ[env_vars[provider]] = api_key


async def generate_operation_spec(
    operation: "Operation",
    config: "LLMConfig",
    stats: UsageStats,
) -> tuple[dict | None, str]:
    """
    Generate OpenAPI path item for a single operation.

    Args:
        operation: The operation to generate spec for
        config: LLM configuration
        stats: Usage stats to update

    Returns:
        Tuple of (path item dict or None, error message if failed)
    """
    import litellm

    litellm.suppress_debug_info = True

    model = config.get("llm_model")
    if not model:
        raise ValueError("LLM model not configured. Run 'apitomcp init' or 'apitomcp auth' to set up.")

    # Build examples section
    examples_section = ""
    if operation.examples:
        examples_section = "EXAMPLES:\n" + "\n\n".join(operation.examples)

    # Build documentation from available content
    documentation = operation.description or ""
    if operation.parameters_text:
        documentation += "\n\n" + operation.parameters_text

    # Truncate if too long
    max_doc_length = 8000
    if len(documentation) > max_doc_length:
        documentation = documentation[:max_doc_length] + "\n[truncated]"

    user_prompt = OPERATION_PROMPT_TEMPLATE.format(
        method=operation.method,
        method_lower=operation.method.lower(),
        path=operation.path,
        documentation=documentation,
        examples_section=examples_section,
    )

    # Retry logic: attempt up to 2 times (1 initial + 1 retry)
    max_attempts = 2
    last_error = None
    
    for attempt in range(max_attempts):
        try:
            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=4000,
                temperature=0.1,
            )

            # Track usage
            stats.add_response(response, model)

            # Extract content
            content = response.choices[0].message.content

            # Parse JSON
            try:
                spec = extract_json(content)
            except ValueError as e:
                last_error = f"Invalid JSON: {str(e)[:50]}"
                # Retry if this was not the last attempt
                if attempt < max_attempts - 1:
                    continue
                return None, last_error

            method_lower = operation.method.lower()
            method_upper = operation.method.upper()
            
            # Check for expected structure: {"get": {...}} or {"GET": {...}}
            if method_lower in spec:
                return spec, ""
            
            if method_upper in spec:
                spec[method_lower] = spec.pop(method_upper)
                return spec, ""
            
            # Handle LLM returning operation directly without method wrapper
            if "operationId" in spec or "operation_id" in spec or "summary" in spec:
                return {method_lower: spec}, ""
            
            # Handle LLM returning path as key: {"/albums": {...}} or {"/albums": {"get": {...}}}
            for key in list(spec.keys()):
                if key.startswith("/"):
                    path_data = spec[key]
                    # Check if it's {"/path": {"get": {...}}}
                    if isinstance(path_data, dict):
                        if method_lower in path_data:
                            return {method_lower: path_data[method_lower]}, ""
                        if method_upper in path_data:
                            return {method_lower: path_data[method_upper]}, ""
                        # It's {"/path": {operationId, summary, ...}} - the operation directly
                        if "operationId" in path_data or "operation_id" in path_data or "summary" in path_data:
                            return {method_lower: path_data}, ""
            
            # Handle nested structure like {"paths": {"/path": {"get": {...}}}}
            if "paths" in spec:
                paths = spec["paths"]
                for path_key, path_item in paths.items():
                    if isinstance(path_item, dict):
                        if method_lower in path_item:
                            return {method_lower: path_item[method_lower]}, ""
                        if method_upper in path_item:
                            return {method_lower: path_item[method_upper]}, ""
            
            # Show what keys were actually returned for debugging
            keys = list(spec.keys())[:3]
            last_error = f"Unexpected format: {keys}"
            # Retry if this was not the last attempt
            if attempt < max_attempts - 1:
                continue
            return None, last_error

        except Exception as e:
            error_msg = str(e)[:50] if str(e) else type(e).__name__
            last_error = f"LLM error: {error_msg}"
            # Retry if this was not the last attempt and it's not a fatal error
            if attempt < max_attempts - 1:
                continue
            return None, last_error
    
    # Should not reach here, but return last error if we do
    return None, last_error or "Generation failed after retries"


async def generate_openapi_spec_parallel(
    operations: list["Operation"],
    config: "LLMConfig",
    base_url: str,
    api_title: str = "API",
) -> tuple[dict, UsageStats]:
    """
    Generate OpenAPI specification from operations using parallel LLM calls.

    Args:
        operations: List of operations to generate specs for
        config: LLM configuration
        base_url: API base URL
        api_title: Title for the API

    Returns:
        Tuple of (OpenAPI spec dict, usage stats)
    """
    global _current_stats

    _set_api_key_env(config)

    stats = UsageStats()
    _current_stats = stats

    # Generate specs for all operations in parallel
    tasks = [generate_operation_spec(op, config, stats) for op in operations]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Build the combined OpenAPI spec
    paths: dict = {}

    for operation, result in zip(operations, results):
        # Handle exceptions from asyncio.gather
        if isinstance(result, Exception):
            stats.operation_results.append(OperationResult(
                method=operation.method,
                path=operation.path,
                status="error",
                error=str(result)[:50],
            ))
            continue
        
        # Unpack the tuple result
        spec_result, error_msg = result if isinstance(result, tuple) else (None, "Unknown error")
        
        if spec_result and isinstance(spec_result, dict):
            # Merge into paths
            path = operation.path
            if path not in paths:
                paths[path] = {}

            method = operation.method.lower()
            if method in spec_result:
                op_data = spec_result[method]
                paths[path][method] = op_data
                stats.operation_results.append(OperationResult(
                    method=operation.method,
                    path=operation.path,
                    status="success",
                    operation_id=op_data.get("operationId", ""),
                    summary=op_data.get("summary", ""),
                ))
            else:
                stats.operation_results.append(OperationResult(
                    method=operation.method,
                    path=operation.path,
                    status="failed",
                    error=error_msg or "Missing method in response",
                ))
        else:
            stats.operation_results.append(OperationResult(
                method=operation.method,
                path=operation.path,
                status="failed",
                error=error_msg or "Generation failed",
            ))

    # Build complete spec
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": api_title,
            "version": "1.0.0",
            "description": f"OpenAPI specification for {api_title}",
        },
        "servers": [{"url": base_url, "description": "API server"}],
        "paths": paths,
        "components": {"securitySchemes": {}, "schemas": {}},
    }

    # Detect common security schemes from the operations
    spec = detect_and_add_security_schemes(spec)

    return spec, stats


def detect_and_add_security_schemes(spec: dict) -> dict:
    """Detect common security patterns and add security schemes."""
    paths_str = json.dumps(spec.get("paths", {})).lower()

    # Check for common auth patterns
    if "bearer" in paths_str or "authorization" in paths_str:
        spec["components"]["securitySchemes"]["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
        }
        spec["security"] = [{"bearerAuth": []}]

    elif "api_key" in paths_str or "apikey" in paths_str or "x-api-key" in paths_str:
        spec["components"]["securitySchemes"]["apiKey"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        }
        spec["security"] = [{"apiKey": []}]

    return spec


def generate_openapi_spec(markdown_content: str, config: "LLMConfig") -> dict:
    """
    Generate an OpenAPI specification from markdown documentation.
    This is the legacy single-call method for fallback.

    Args:
        markdown_content: The scraped and converted documentation
        config: LLM configuration with provider and API key

    Returns:
        Parsed OpenAPI specification as a dictionary
    """
    import litellm

    litellm.suppress_debug_info = True
    _set_api_key_env(config)

    model = config.get("llm_model")
    if not model:
        raise ValueError("LLM model not configured. Run 'apitomcp init' or 'apitomcp auth' to set up.")

    legacy_system_prompt = """You are an expert API documentation analyzer and OpenAPI specification generator.

Your task is to analyze the provided API documentation and generate a valid OpenAPI 3.1.0 specification in JSON format.

CRITICAL RULES:
1. ONLY include endpoints that are explicitly documented in the provided content
2. DO NOT invent or hallucinate any endpoints, parameters, or response schemas
3. If information is unclear or missing, use reasonable defaults but be conservative
4. All paths must be valid URL paths starting with /
5. All HTTP methods must be lowercase (get, post, put, patch, delete)
6. Include proper security schemes if authentication is documented
7. Use descriptive operationIds in snake_case format

OUTPUT FORMAT:
- Return ONLY valid JSON, no markdown code blocks
- The JSON must be a complete OpenAPI 3.1.0 specification
- Do not include any explanatory text before or after the JSON

SCHEMA REQUIREMENTS:
- openapi: "3.1.0"
- info: title, version (default "1.0.0"), description
- servers: array with at least one server URL
- paths: documented endpoints with operations
- components: schemas and securitySchemes as needed"""

    user_prompt = f"""Analyze the following API documentation and generate a complete OpenAPI 3.1.0 specification in JSON format.

API DOCUMENTATION:
{markdown_content[:50000]}

Generate the OpenAPI specification now. Return ONLY the JSON, no other text."""

    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": legacy_system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=16000,
        temperature=0.1,
    )

    # Track usage if stats available
    global _current_stats
    if _current_stats:
        _current_stats.add_response(response, model)

    content = response.choices[0].message.content
    spec = extract_json(content)

    return spec


def generate_openapi_spec_with_errors(
    markdown_content: str,
    config: "LLMConfig",
    previous_spec: str,
    errors: list[str],
) -> dict:
    """
    Regenerate an OpenAPI spec after validation errors.

    Args:
        markdown_content: The original documentation
        config: LLM configuration
        previous_spec: The previously generated spec that failed validation
        errors: List of validation error messages

    Returns:
        Corrected OpenAPI specification
    """
    import litellm

    litellm.suppress_debug_info = True
    _set_api_key_env(config)

    model = config.get("llm_model")
    if not model:
        raise ValueError("LLM model not configured. Run 'apitomcp init' or 'apitomcp auth' to set up.")

    error_text = "\n".join(f"- {e}" for e in errors[:10])

    user_prompt = f"""The previously generated OpenAPI specification had validation errors.

ORIGINAL DOCUMENTATION:
{markdown_content[:50000]}

PREVIOUS SPEC (with errors):
{previous_spec[:10000]}

VALIDATION ERRORS:
{error_text}

Please fix these errors and generate a corrected OpenAPI 3.1.0 specification.
Return ONLY the corrected JSON, no other text."""

    response = litellm.completion(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=16000,
        temperature=0.1,
    )

    # Track usage
    global _current_stats
    if _current_stats:
        _current_stats.add_response(response, model)

    content = response.choices[0].message.content
    spec = extract_json(content)

    return spec


def extract_json(content: str) -> dict:
    """
    Extract JSON from LLM response, handling various formats.

    Args:
        content: The raw LLM response

    Returns:
        Parsed JSON as a dictionary

    Raises:
        ValueError: If no valid JSON could be extracted
    """
    content = content.strip()

    # Try to find JSON in code blocks
    code_block_patterns = [
        r"```json\s*([\s\S]*?)\s*```",
        r"```\s*([\s\S]*?)\s*```",
    ]

    for pattern in code_block_patterns:
        match = re.search(pattern, content)
        if match:
            content = match.group(1).strip()
            break

    # Try to parse directly
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object boundaries
    start_idx = content.find("{")
    if start_idx != -1:
        depth = 0
        for i, char in enumerate(content[start_idx:], start_idx):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(content[start_idx : i + 1])
                    except json.JSONDecodeError:
                        pass
                    break

    raise ValueError("Could not extract valid JSON from LLM response")


def merge_specs(base_spec: dict, additional_spec: dict) -> dict:
    """
    Merge two OpenAPI specs, combining paths and components.

    Args:
        base_spec: The base specification
        additional_spec: Additional spec to merge in

    Returns:
        Merged specification
    """
    # Merge paths
    base_paths = base_spec.get("paths", {})
    additional_paths = additional_spec.get("paths", {})

    for path, path_item in additional_paths.items():
        if path not in base_paths:
            base_paths[path] = path_item
        else:
            # Merge methods
            for method, operation in path_item.items():
                if method not in base_paths[path]:
                    base_paths[path][method] = operation

    base_spec["paths"] = base_paths

    # Merge components
    base_components = base_spec.get("components", {})
    additional_components = additional_spec.get("components", {})

    for component_type, components in additional_components.items():
        if component_type not in base_components:
            base_components[component_type] = components
        else:
            base_components[component_type].update(components)

    base_spec["components"] = base_components

    return base_spec


def get_current_usage_stats() -> UsageStats | None:
    """Get the current usage stats from the last generation."""
    return _current_stats


def reset_usage_stats() -> None:
    """Reset usage stats for a new generation."""
    global _current_stats
    _current_stats = UsageStats()
