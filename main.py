from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from fastapi.responses import StreamingResponse

app = FastAPI()

mcp = FastApiMCP(
    app,
    name="Welfare MCP Server",
    description="MCP server for welfare services"
)

@mcp.tool()
def ping() -> StreamingResponse:
    return "pong"

mcp.mount_http(app,"/mcp")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)