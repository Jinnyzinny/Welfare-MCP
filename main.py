from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

app = FastAPI()

mcp = FastApiMCP(
    app,
    name="Welfare MCP Server",
    description="MCP server for welfare services"
)
mcp.mount_http()

# Auto-generated operation_id (something like "read_user_users__user_id__get")
@app.get("/")
async def read_user(user_id: int):
    return {"user_id": user_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)