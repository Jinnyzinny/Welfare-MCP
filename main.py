from mcp_container import mcp


from starlette.middleware.cors import CORSMiddleware
from mcp_container import app,base_app

# if __name__ == "__main__":
#     mcp.run(transport="streamable-http", 
#             mount_path="/mcp", 
#             host="localhost", 
#             port=8000)

# Then wrap it with CORS middleware
app = CORSMiddleware(
    base_app,
    allow_origins=["*"],  # Configure appropriately for production
    allow_methods=["GET", "POST", "DELETE"],  # MCP streamable HTTP methods
    expose_headers=["Mcp-Session-Id"],
)