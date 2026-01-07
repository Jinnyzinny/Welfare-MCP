import contextlib

# mcp_container.py
from mcp.server.fastmcp import FastMCP

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.cors import CORSMiddleware

# Create an MCP server
mcp = FastMCP(
    name="Welfare MCP Server",
    stateless_http=True,
    json_response=True,
)

mcp_http_app = mcp.streamable_http_app()

# Create a combined lifespan to manage both session managers
# lifespan: 세션 매니저 필수
@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield


app=Starlette(
    routes=[
        Mount("/mcp", mcp_http_app) ,
    ],
    lifespan=lifespan,
)

# Then wrap it with CORS middleware
starlette_app = CORSMiddleware(
    app,
    allow_origins=["*"],  # Configure appropriately for production
    allow_methods=["GET", "POST", "DELETE"],  # MCP streamable HTTP methods
    expose_headers=["Mcp-Session-Id"],
)