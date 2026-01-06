from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from fastapi.responses import StreamingResponse

app = FastAPI()

mcp = FastApiMCP(
    app,
    name="Welfare MCP Server",
    description="MCP server for welfare services"
)
mcp.mount_http()

@app.get("/mcp")
async def read_mcp():
    return StreamingResponse(
        media_type={"text/event-stream", "application/json"},
        content={"message": "MCP server is running"})

@app.post("/mcp")
async def post_mcp():
    return StreamingResponse(
        media_type={"text/event-stream", "application/json"},
        content={"message": "MCP server received a POST request"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

mcp.mount(app)