from mcp_container import mcp

from starlette.middleware.cors import CORSMiddleware

from welfare_mcp.tools.user_profile import collect_basic_profile, collect_household_profile
from welfare_mcp.tools.check_eligibility import check_eligibility
from welfare_mcp.tools.required_documents import required_documents

# from mcp_container import app
if __name__ == "__main__":
    mcp.run(transport="streamable-http", 
            mount_path="/mcp", 
            host="localhost", 
            port=8000)

# Then wrap it with CORS middleware
# app = CORSMiddleware(
#     base_app,
#     allow_origins=["*"],  # Configure appropriately for production
#     allow_methods=["GET", "POST", "DELETE"],  # MCP streamable HTTP methods
#     expose_headers=["Mcp-Session-Id"],
# )