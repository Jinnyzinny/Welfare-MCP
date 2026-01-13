from mcp_container import mcp

from tools.user_profile import collect_basic_profile, collect_household_profile
from tools.check_eligibility import check_eligibility
from tools.required_documents import required_documents

# from mcp_container import app
if __name__ == "__main__":
    mcp.run(transport="streamable-http", 
            mount_path="/mcp"
            )