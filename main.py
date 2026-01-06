from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from fastapi.responses import StreamingResponse
from httpx import AsyncClient

app = FastAPI()

mcp = FastApiMCP(
    app,
    name="Welfare MCP Server",
    description="MCP server for welfare services",
    http_client=httpx.AsyncClient(timeout=10.0)
)

mcp.mount_http(app,"/mcp")

@app.get("/")
async def root():
    return {"message": "Welcome to the Welfare MCP Server"}

@app.post("/")
async def proxy_request():
    async with AsyncClient(timeout=10.0) as client:
        response = await client.post("http://example.com/api", json={"key": "value"})
        return StreamingResponse(response.aiter_bytes(), status_code=response.status_code, headers=response.headers)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)