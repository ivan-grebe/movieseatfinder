"""Public request handling and throttling for the MCP endpoint."""

from limits import RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse

MCP_GLOBAL_RATE_LIMIT = RateLimitItemPerMinute(1200)
MCP_RATE_LIMITER = MovingWindowRateLimiter(MemoryStorage())

MCP_BROWSER_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Movie Seat Finder MCP</title>
  <link rel="stylesheet" href="/mcp.bundle.css">
</head>
<body>
  <main>
    <span class="eyebrow">Connection endpoint</span>
    <h1>Movie Seat Finder MCP</h1>
    <p>This URL connects MCP-compatible assistants to Movie Seat Finder. There is nothing you need to configure on this page.</p>
    <a href="/">Open Movie Seat Finder</a>
  </main>
</body>
</html>"""


def _is_browser_navigation(scope, headers):
    if scope.get("method", "").upper() != "GET":
        return False
    return (
        headers.get("sec-fetch-mode", "").lower() == "navigate"
        or headers.get("sec-fetch-dest", "").lower() == "document"
        or "text/html" in headers.get("accept", "").lower()
    )


def _browser_response():
    return HTMLResponse(
        MCP_BROWSER_PAGE,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'"
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

        if not MCP_RATE_LIMITER.hit(MCP_GLOBAL_RATE_LIMIT, "/mcp", "global", "all"):
            response = JSONResponse(
                {"error": "Too many MCP requests. Please wait a moment and try again."},
                status_code=429,
                headers={"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
