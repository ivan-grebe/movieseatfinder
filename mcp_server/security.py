"""Authentication and per-user throttling for the remote MCP endpoint."""

import os
import secrets

from limits import RateLimitItemPerSecond
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

MCP_RATE_LIMIT = RateLimitItemPerSecond(60, 60)
MCP_RATE_LIMITER = MovingWindowRateLimiter(MemoryStorage())


def _client_key(scope, headers):
    poke_user_id = headers.get("x-poke-user-id", "").strip()
    if poke_user_id:
        return f"poke:{poke_user_id[:128]}"
    forwarded_for = headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded_for:
        return f"ip:{forwarded_for}"
    client = scope.get("client")
    return f"ip:{client[0] if client else 'unknown'}"


class McpSecurityMiddleware:
    """Require the shared Poke credential before MCP protocol handling."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        expected_key = os.environ.get("MCP_API_KEY", "").strip()
        if not expected_key:
            response = JSONResponse(
                {"error": "The MCP integration is not configured."},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        authorization = headers.get("authorization", "")
        scheme, separator, supplied_key = authorization.partition(" ")
        authorized = (
            separator == " "
            and scheme.lower() == "bearer"
            and bool(supplied_key)
            and secrets.compare_digest(supplied_key, expected_key)
        )
        if not authorized:
            response = JSONResponse(
                {"error": "A valid MCP API key is required."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        if not MCP_RATE_LIMITER.hit(MCP_RATE_LIMIT, "/mcp", _client_key(scope, headers)):
            response = JSONResponse(
                {"error": "Too many MCP requests. Please wait a moment and try again."},
                status_code=429,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
