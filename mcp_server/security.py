"""Public-Poke request validation and throttling for the MCP endpoint."""

import base64
import hashlib
import os
import secrets

from limits import RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from starlette.datastructures import Headers
from starlette.responses import HTMLResponse, JSONResponse

MCP_USER_RATE_LIMIT = RateLimitItemPerMinute(60)
MCP_GLOBAL_RATE_LIMIT = RateLimitItemPerMinute(1200)
MCP_RATE_LIMITER = MovingWindowRateLimiter(MemoryStorage())

MCP_BROWSER_STYLE = """
:root { color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
* { box-sizing: border-box; }
body {
  min-height: 100svh;
  margin: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  color: #172033;
  background:
    radial-gradient(circle at 18% 8%, rgba(255, 138, 122, .34), transparent 36%),
    radial-gradient(circle at 82% 12%, rgba(120, 165, 255, .3), transparent 34%),
    #fff8f7;
  -webkit-font-smoothing: antialiased;
}
main {
  width: min(100%, 560px);
  padding: 32px;
  border-radius: 24px;
  background: rgba(255, 255, 255, .82);
  box-shadow: 0 0 0 1px rgba(0, 0, 0, .06), 0 24px 64px rgba(42, 58, 86, .16);
  backdrop-filter: blur(18px);
}
.eyebrow {
  color: #8f1f26;
  font-size: 12px;
  font-weight: 850;
  letter-spacing: .12em;
  text-transform: uppercase;
}
h1 { margin: 10px 0 12px; font-size: clamp(30px, 7vw, 44px); letter-spacing: -.045em; text-wrap: balance; }
p { margin: 0; color: #58677d; line-height: 1.65; text-wrap: pretty; }
a {
  min-height: 44px;
  margin-top: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 18px;
  border-radius: 12px;
  color: white;
  background: linear-gradient(135deg, #c93a3a, #8f1f26);
  box-shadow: 0 8px 18px rgba(143, 31, 38, .22);
  font-weight: 750;
  text-decoration: none;
  transition-property: transform, filter;
  transition-duration: .15s;
}
a:hover { filter: saturate(1.08); }
a:active { transform: scale(.96); }
a:focus-visible { outline: 2px solid #8f1f26; outline-offset: 3px; }
@media (prefers-color-scheme: dark) {
  body {
    color: #e8edf4;
    background:
      radial-gradient(circle at 18% 8%, rgba(210, 67, 60, .32), transparent 36%),
      radial-gradient(circle at 82% 12%, rgba(215, 157, 78, .24), transparent 34%),
      #0b0d12;
  }
  main {
    background: rgba(22, 27, 36, .84);
    box-shadow: 0 0 0 1px rgba(255, 255, 255, .08), 0 24px 64px rgba(0, 0, 0, .52);
  }
  .eyebrow { color: #f0a19b; }
  p { color: #aeb9c9; }
}
""".strip()
MCP_BROWSER_STYLE_HASH = base64.b64encode(
    hashlib.sha256(MCP_BROWSER_STYLE.encode()).digest()
).decode()
MCP_BROWSER_PAGE = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Movie Seat Finder MCP</title>
  <style>{MCP_BROWSER_STYLE}</style>
</head>
<body>
  <main>
    <span class="eyebrow">Connection endpoint</span>
    <h1>Movie Seat Finder MCP</h1>
    <p>This URL connects assistants such as Poke to Movie Seat Finder. There is nothing you need to configure on this page.</p>
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
                "default-src 'none'; "
                f"style-src 'sha256-{MCP_BROWSER_STYLE_HASH}'; "
                "base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
        if _is_browser_navigation(scope, headers):
            await _browser_response()(scope, receive, send)
            return

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
