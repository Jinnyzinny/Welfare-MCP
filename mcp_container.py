import contextlib

# mcp_container.py
from mcp.server.fastmcp import FastMCP

from starlette.applications import Starlette
from starlette.routing import Mount,Host
from starlette.middleware.cors import CORSMiddleware

from starlette.middleware.trustedhost import TrustedHostMiddleware

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


base_app=Starlette(
    routes=[
        Host("welfare-mcpserver.shop"),
        Mount("/", mcp_http_app),
    ],
    lifespan=lifespan,
)

# base_app.add_middleware(
#     TrustedHostMiddleware, allowed_hosts=["welfare-mcpserver.shop", "localhost", "*.shop"]
# )

# base_app 정의 아래에 추가하여 실제 등록된 경로 확인
for route in base_app.routes:
    print(f"Route: {route.path}")
    if hasattr(route, 'app') and hasattr(route.app, 'routes'):
        for sub_route in route.app.routes:
            print(f"  -> Sub-Route: {sub_route.path}")

# Then wrap it with CORS middleware
app = CORSMiddleware(
    base_app,
    allow_origins=["*"],  # Configure appropriately for production
    allow_headers=["*"],  # Allow all headers
    allow_methods=["GET", "POST", "DELETE"],  # MCP streamable HTTP methods
    expose_headers=["Mcp-Session-Id"],
)

mcp_http_app.state.allow_origins = ["*"]  # Configure appropriately for production