"""Public request handling and throttling for the MCP endpoint."""

from pathlib import Path

from limits import RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse

MCP_GLOBAL_RATE_LIMIT = RateLimitItemPerMinute(1200)
# Stops one client from using the whole global budget. Kept generous because
# hosted MCP clients (e.g. chat apps) send many users' calls from shared IPs.
MCP_CLIENT_RATE_LIMIT = RateLimitItemPerMinute(300)
MCP_RATE_LIMITER = MovingWindowRateLimiter(MemoryStorage())

MCP_BROWSER_PAGE = (
    Path(__file__).resolve().parents[2] / "frontend" / "templates" / "mcp.html"
).read_text(encoding="utf-8")


def _is_browser_navigation(scope, headers):
    if scope.get("method", "").upper() != "GET":
        return False
    return (
        headers.get("sec-fetch-mode", "").lower() == "navigate"
        or headers.get("sec-fetch-dest", "").lower() == "document"
        or "text/html" in headers.get("accept", "").lower()
    )


def _client_key(scope, headers):
    """Real caller IP: the first x-forwarded-for hop behind the hosting proxy."""
    first_hop = headers.get("x-forwarded-for", "").split(",")[0].strip()
    if first_hop:
        return first_hop
    client = scope.get("client")
    return client[0] if client else "unknown"


def _browser_response():
    return HTMLResponse(
        MCP_BROWSER_PAGE,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'self'; script-src 'self'; "
                "font-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


class McpSecurityMiddleware:
    """Accept public MCP traffic while containing anonymous abuse."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if _is_browser_navigation(scope, headers):
            await _browser_response()(scope, receive, send)
            return

        within_client_limit = MCP_RATE_LIMITER.hit(
            MCP_CLIENT_RATE_LIMIT, "/mcp", "client", _client_key(scope, headers)
        )
        if not within_client_limit or not MCP_RATE_LIMITER.hit(
            MCP_GLOBAL_RATE_LIMIT, "/mcp", "global", "all"
        ):
            response = JSONResponse(
                {"error": "Too many MCP requests. Please wait a moment and try again."},
                status_code=429,
                headers={"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
