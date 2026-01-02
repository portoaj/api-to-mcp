"""OpenAPI specification validation with retry logic."""

import json
from typing import Callable

from openapi_spec_validator import validate
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

from apitomcp.config import LLMConfig
from apitomcp.ui import print_error, print_info, print_success, spinner


def validate_openapi_spec(spec: dict) -> tuple[bool, list[str]]:
    """
    Validate an OpenAPI specification.

    Args:
        spec: The OpenAPI specification to validate

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors: list[str] = []

    # Basic structure checks
    if not isinstance(spec, dict):
        return False, ["Specification must be a JSON object"]

    if "openapi" not in spec:
        errors.append("Missing required field: openapi")

    if "info" not in spec:
        errors.append("Missing required field: info")

    if "paths" not in spec:
        errors.append("Missing required field: paths")

    # Check OpenAPI version
    openapi_version = spec.get("openapi", "")
    if not openapi_version.startswith("3."):
        errors.append(f"Invalid OpenAPI version: {openapi_version}. Expected 3.x")

    # Check info section
    info = spec.get("info", {})
    if not isinstance(info, dict):
        errors.append("info must be an object")
    elif "title" not in info:
        errors.append("Missing required field: info.title")

    # Check paths
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        errors.append("paths must be an object")
    elif not paths:
        errors.append("paths cannot be empty - no endpoints defined")

    # Validate path structure
    for path, path_item in paths.items():
        if not path.startswith("/"):
            errors.append(f"Path must start with '/': {path}")

        if not isinstance(path_item, dict):
            errors.append(f"Path item must be an object: {path}")
            continue

        # Check operations
        valid_methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
        for key in path_item:
            if key in valid_methods:
                operation = path_item[key]
                if not isinstance(operation, dict):
                    errors.append(f"Operation must be an object: {path}.{key}")

    if errors:
        return False, errors

    # Use openapi-spec-validator for full validation
    try:
        validate(spec)
        return True, []
    except OpenAPIValidationError as e:
        return False, [str(e)]
    except Exception as e:
        return False, [f"Validation error: {str(e)}"]


def validate_and_retry(
    generate_fn: Callable[[], dict],
    config: LLMConfig,
    max_retries: int = 3,
) -> dict:
    """
    Generate and validate an OpenAPI spec, retrying on failure.

    Args:
        generate_fn: Function that generates the initial spec
        config: LLM configuration for retry attempts
        max_retries: Maximum number of retry attempts

    Returns:
        Validated OpenAPI specification

    Raises:
        RuntimeError: If validation fails after all retries
    """
    from apitomcp.generator import generate_openapi_spec_with_errors

    spec: dict = {}
    last_errors: list[str] = []

    for attempt in range(max_retries + 1):
        if attempt == 0:
            with spinner("Generating OpenAPI specification..."):
                try:
                    spec = generate_fn()
                except Exception as e:
                    raise RuntimeError(f"Failed to generate spec: {e}")
        else:
            print_info(f"Retry attempt {attempt}/{max_retries}...")
            with spinner("Regenerating with error feedback..."):
                try:
                    # We need markdown_content for retry, but generate_fn closure has it
                    # This is a workaround - we'll pass the previous spec as JSON
                    spec = generate_openapi_spec_with_errors(
                        "",  # Empty - the LLM has context from previous attempt
                        config,
                        json.dumps(spec, indent=2),
                        last_errors,
                    )
                except Exception as e:
                    print_error(f"Regeneration failed: {e}")
                    continue

        # Validate the spec
        with spinner("Validating specification..."):
            is_valid, errors = validate_openapi_spec(spec)

        if is_valid:
            print_success("OpenAPI specification is valid!")
            return spec

        last_errors = errors
        print_error(f"Validation failed with {len(errors)} error(s):")
        for error in errors[:5]:  # Show first 5 errors
            print_error(f"  - {error}")
        if len(errors) > 5:
            print_error(f"  ... and {len(errors) - 5} more errors")

    raise RuntimeError(f"Failed to generate valid spec after {max_retries} retries")
