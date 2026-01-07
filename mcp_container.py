# mcp_container.py
import httpx
from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

app = FastAPI()
mcp = FastApiMCP(
    app,
    name="Welfare MCP Server",
    description="MCP server for welfare services",
)