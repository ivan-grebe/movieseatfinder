"""Show a readable page to people who open /mcp in a browser."""

from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import HTMLResponse

MCP_BROWSER_PAGE = (Path(__file__).resolve().parents[1] / "templates" / "mcp.html").read_text(
    encoding="utf-8"
)


def _is_browser_navigation(scope):
    if scope["method"] != "GET":
        return False
    headers = Headers(scope=scope)
    return (
        headers.get("sec-fetch-mode") == "navigate"
        or headers.get("sec-fetch-dest") == "document"
        or "text/html" in headers.get("accept", "")
    )


class BrowserPageMiddleware:
    """Answer browser visits with the MCP info page; pass everything else to the MCP app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if _is_browser_navigation(scope):
            response = HTMLResponse(
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
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
