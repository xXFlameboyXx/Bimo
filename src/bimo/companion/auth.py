"""Authentication and security middleware for Bimo Companion API."""

from __future__ import annotations

import hmac
import os
import secrets
from typing import Optional
from fastapi import Header, HTTPException, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)

# Configurable companion auth token from environment or persistent secret
COMPANION_TOKEN_ENV = "BIMO_COMPANION_TOKEN"
_DEFAULT_LOCAL_TOKEN = os.environ.get(COMPANION_TOKEN_ENV, "bimo-companion-secret-2026")


def get_expected_token() -> str:
    """Return configured companion authentication token."""
    return os.environ.get(COMPANION_TOKEN_ENV, _DEFAULT_LOCAL_TOKEN)


def verify_companion_token(
    auth_header: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_bimo_auth: Optional[str] = Header(None, alias="X-Bimo-Auth"),
    token_query: Optional[str] = Query(None, alias="token"),
) -> bool:
    """Validate token across Authorization header, X-Bimo-Auth, or query param (for WebSocket)."""
    expected = get_expected_token()
    token = None
    if auth_header and auth_header.credentials:
        token = auth_header.credentials
    elif x_bimo_auth:
        token = x_bimo_auth
    elif token_query:
        token = token_query

    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Companion authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True
