"""Vercel-compatible composition root for the website and MCP server."""

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from backend.application import app as website_app
from mcp_server.server import mcp_protocol_app, mcp_route


@asynccontextmanager
async def lifespan(_app):
    async with mcp_protocol_app.router.lifespan_context(mcp_protocol_app):
        yield


app = Starlette(
    routes=[
        mcp_route,
        Mount("/", app=website_app),
    ],
    lifespan=lifespan,
)

__all__ = ["app"]
