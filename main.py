import uvicorn
from mcp_container import app, mcp

from welfare_mcp.tools import user_profile, required_documents, check_eligibility
mcp.mount_http(app,"/mcp")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)