"""OAuth2 and authentication handling for API servers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx


@dataclass
class AuthConfig:
    """Authentication configuration for an API."""

    type: str = "none"  # "none", "bearer", "api_key", "oauth2_client_credentials"
    
    # For bearer/api_key
    header_name: str = "Authorization"
    value_prefix: str = ""
    env_var: str = ""
    value: str = ""  # Static token/key
    
    # For OAuth2 client credentials
    client_id: str = ""
    client_secret: str = ""
    token_url: str = ""
    scope: str = ""
    
    # Cached OAuth2 token
    access_token: str = ""
    token_expires_at: datetime | None = None
    refresh_token: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuthConfig":
        """Create AuthConfig from a dictionary."""
        config = cls()
        
        config.type = data.get("type", "none")
        config.header_name = data.get("header_name", "Authorization")
        config.value_prefix = data.get("value_prefix", "")
        config.env_var = data.get("env_var", "")
        config.value = data.get("value", "")
        config.client_id = data.get("client_id", "")
        config.client_secret = data.get("client_secret", "")
        config.token_url = data.get("token_url", "")
        config.scope = data.get("scope", "")
        config.access_token = data.get("access_token", "")
        config.refresh_token = data.get("refresh_token", "")
        
        # Parse expires_at if present
        expires_at = data.get("token_expires_at")
        if expires_at:
            if isinstance(expires_at, str):
                try:
                    config.token_expires_at = datetime.fromisoformat(expires_at)
                except ValueError:
                    pass
            elif isinstance(expires_at, datetime):
                config.token_expires_at = expires_at
        
        return config

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        data = {
            "type": self.type,
            "header_name": self.header_name,
            "value_prefix": self.value_prefix,
            "env_var": self.env_var,
        }
        
        if self.value:
            data["value"] = self.value
        
        if self.type == "oauth2_client_credentials":
            data["client_id"] = self.client_id
            data["client_secret"] = self.client_secret
            data["token_url"] = self.token_url
            if self.scope:
                data["scope"] = self.scope
            if self.access_token:
                data["access_token"] = self.access_token
            if self.refresh_token:
                data["refresh_token"] = self.refresh_token
            if self.token_expires_at:
                data["token_expires_at"] = self.token_expires_at.isoformat()
        
        return data

    def is_token_expired(self) -> bool:
        """Check if the OAuth2 token is expired or about to expire."""
        if not self.token_expires_at:
            return True
        
        # Consider expired if less than 60 seconds remaining
        now = datetime.now(timezone.utc)
        expires_at = self.token_expires_at
        
        # Ensure timezone aware
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        
        buffer = 60  # seconds
        return (expires_at.timestamp() - now.timestamp()) < buffer

    def needs_refresh(self) -> bool:
        """Check if we need to refresh/fetch a new token."""
        if self.type != "oauth2_client_credentials":
            return False
        
        # Need refresh if no token or token expired
        if not self.access_token:
            return True
        
        return self.is_token_expired()


async def fetch_oauth2_token(auth_config: AuthConfig) -> AuthConfig:
    """
    Fetch a new OAuth2 access token using client credentials flow.
    
    Args:
        auth_config: Auth configuration with client credentials
        
    Returns:
        Updated auth config with new access token
        
    Raises:
        RuntimeError: If token fetch fails
    """
    if not auth_config.token_url:
        raise RuntimeError("No token URL configured for OAuth2")
    
    if not auth_config.client_id or not auth_config.client_secret:
        raise RuntimeError("Client ID and secret required for OAuth2")
    
    # Build token request
    data = {
        "grant_type": "client_credentials",
        "client_id": auth_config.client_id,
        "client_secret": auth_config.client_secret,
    }
    
    if auth_config.scope:
        data["scope"] = auth_config.scope
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                auth_config.token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to fetch OAuth2 token: {e}")
        
        try:
            token_data = response.json()
        except Exception as e:
            raise RuntimeError(f"Invalid token response: {e}")
    
    # Update auth config with new token
    auth_config.access_token = token_data.get("access_token", "")
    
    if not auth_config.access_token:
        raise RuntimeError("No access_token in OAuth2 response")
    
    # Calculate expiration
    expires_in = token_data.get("expires_in")
    if expires_in:
        auth_config.token_expires_at = datetime.now(timezone.utc).replace(
            microsecond=0
        )
        from datetime import timedelta
        auth_config.token_expires_at += timedelta(seconds=int(expires_in))
    else:
        # Default to 1 hour if not specified
        from datetime import timedelta
        auth_config.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    
    # Store refresh token if provided
    if "refresh_token" in token_data:
        auth_config.refresh_token = token_data["refresh_token"]
    
    return auth_config


def fetch_oauth2_token_sync(auth_config: AuthConfig) -> AuthConfig:
    """
    Synchronous version of fetch_oauth2_token for non-async contexts.
    """
    import asyncio
    
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(fetch_oauth2_token(auth_config))


async def get_auth_headers(auth_config: AuthConfig) -> dict[str, str]:
    """
    Get authentication headers, refreshing OAuth2 token if needed.
    
    Args:
        auth_config: Auth configuration
        
    Returns:
        Dictionary of headers to include in requests
    """
    import os
    
    headers: dict[str, str] = {}
    
    if auth_config.type == "none":
        return headers
    
    if auth_config.type == "oauth2_client_credentials":
        # Check if we need to refresh token
        if auth_config.needs_refresh():
            auth_config = await fetch_oauth2_token(auth_config)
        
        # Use the access token
        token = auth_config.access_token
        headers[auth_config.header_name] = f"{auth_config.value_prefix}{token}"
        
    elif auth_config.type in ("bearer", "api_key"):
        # Get value from config or environment
        value = auth_config.value
        
        if not value and auth_config.env_var:
            value = os.environ.get(auth_config.env_var, "")
        
        if value:
            headers[auth_config.header_name] = f"{auth_config.value_prefix}{value}"
    
    return headers


def get_auth_headers_sync(auth_config: AuthConfig) -> dict[str, str]:
    """
    Synchronous version of get_auth_headers.
    """
    import asyncio
    
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(get_auth_headers(auth_config))


def detect_oauth2_from_spec(spec: dict) -> dict[str, Any] | None:
    """
    Detect OAuth2 configuration from an OpenAPI spec.
    
    Args:
        spec: OpenAPI specification
        
    Returns:
        OAuth2 configuration dict or None if not found
    """
    security_schemes = spec.get("components", {}).get("securitySchemes", {})
    
    for scheme_name, scheme in security_schemes.items():
        if scheme.get("type") == "oauth2":
            flows = scheme.get("flows", {})
            
            # Check for client credentials flow
            client_creds = flows.get("clientCredentials", {})
            if client_creds:
                return {
                    "type": "oauth2_client_credentials",
                    "token_url": client_creds.get("tokenUrl", ""),
                    "scope": " ".join(client_creds.get("scopes", {}).keys()),
                }
            
            # Check for authorization code flow (might have token URL)
            auth_code = flows.get("authorizationCode", {})
            if auth_code:
                return {
                    "type": "oauth2_client_credentials",  # We'll use client creds
                    "token_url": auth_code.get("tokenUrl", ""),
                    "scope": " ".join(auth_code.get("scopes", {}).keys()),
                }
    
    return None


def detect_auth_type_from_spec(spec: dict) -> dict[str, Any]:
    """
    Detect authentication type and configuration from OpenAPI spec.
    
    Args:
        spec: OpenAPI specification
        
    Returns:
        Auth configuration dictionary
    """
    auth_config: dict[str, Any] = {"type": "none"}
    
    security_schemes = spec.get("components", {}).get("securitySchemes", {})
    
    for scheme_name, scheme in security_schemes.items():
        scheme_type = scheme.get("type", "")
        
        # Check for OAuth2 first
        if scheme_type == "oauth2":
            oauth_config = detect_oauth2_from_spec(spec)
            if oauth_config:
                oauth_config["header_name"] = "Authorization"
                oauth_config["value_prefix"] = "Bearer "
                oauth_config["env_var"] = f"{scheme_name.upper()}_ACCESS_TOKEN"
                return oauth_config
        
        # HTTP Bearer auth
        if scheme_type == "http" and scheme.get("scheme") == "bearer":
            return {
                "type": "bearer",
                "header_name": "Authorization",
                "value_prefix": "Bearer ",
                "env_var": f"{scheme_name.upper()}_TOKEN",
            }
        
        # API Key
        if scheme_type == "apiKey":
            return {
                "type": "api_key",
                "header_name": scheme.get("name", "X-API-Key"),
                "value_prefix": "",
                "env_var": f"{scheme_name.upper()}_API_KEY",
            }
    
    return auth_config
