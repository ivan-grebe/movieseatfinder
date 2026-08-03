"""Public-Poke request validation and throttling for the MCP endpoint."""

import os
import secrets

from limits import RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

MCP_USER_RATE_LIMIT = RateLimitItemPerMinute(60)
MCP_IP_RATE_LIMIT = RateLimitItemPerMinute(180)
MCP_GLOBAL_RATE_LIMIT = RateLimitItemPerMinute(1200)
MCP_RATE_LIMITER = MovingWindowRateLimiter(MemoryStorage())


def _client_ip(scope, headers):
    real_ip = headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip[:128]
    forwarded_for = headers.get("x-forwarded-for", "").split(",")[-1].strip()
    if forwarded_for:
        return forwarded_for[:128]
    client = scope.get("client")
    return client[0] if client else "unknown"


def _valid_poke_user_id(headers):
    poke_user_id = headers.get("x-poke-user-id", "").strip()
    if not poke_user_id or len(poke_user_id) > 128:
        return ""
    if not all(character.isalnum() or character in "-_.:" for character in poke_user_id):
        return ""
    return poke_user_id


def _valid_optional_bearer(headers):
    authorization = headers.get("authorization", "").strip()
    if not authorization:
        return True
    expected_key = os.environ.get("MCP_API_KEY", "").strip()
    scheme, separator, supplied_key = authorization.partition(" ")
    return (
        bool(expected_key)
        and separator == " "
        and scheme.lower() == "bearer"
        and bool(supplied_key)
        and secrets.compare_digest(supplied_key, expected_key)
    )


class McpSecurityMiddleware:
    """Accept public Poke traffic while containing anonymous abuse."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        supplied_poke_user_id = headers.get("x-poke-user-id", "").strip()
        poke_user_id = _valid_poke_user_id(headers)
        if supplied_poke_user_id and not poke_user_id:
            response = JSONResponse(
                {"error": "The supplied X-Poke-User-Id header is invalid."},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        if not _valid_optional_bearer(headers):
            response = JSONResponse(
                {"error": "The supplied MCP API key is invalid."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        rate_limits = [
            (MCP_GLOBAL_RATE_LIMIT, "global", "all"),
            (MCP_IP_RATE_LIMIT, "ip", _client_ip(scope, headers)),
        ]
        if poke_user_id:
            rate_limits.append((MCP_USER_RATE_LIMIT, "poke", poke_user_id))
        for rate_limit, dimension, value in rate_limits:
            if not MCP_RATE_LIMITER.hit(rate_limit, "/mcp", dimension, value):
                response = JSONResponse(
                    {"error": "Too many MCP requests. Please wait a moment and try again."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
