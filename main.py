from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

import httpx
from httpx import AsyncClient

import mcp_container

app = FastAPI()

mcp = FastApiMCP(
    app,
    name="Welfare MCP Server",
    description="MCP server for welfare services",
    http_client=httpx.AsyncClient(timeout=10.0)
)

from welfare_mcp.tools import user_profile, required_documents, check_eligibility

mcp.mount_http(app,"/mcp")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)