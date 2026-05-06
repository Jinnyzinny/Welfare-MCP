import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from welfare_mcp.mcp_container import mcp, app

# from tools.user_profile import collect_basic_profile, collect_household_profile
from tools.check_eligibility import check_eligibility

from prompt.check_eligibility import initial_onboarding_prompt

from dotenv import load_dotenv
load_dotenv()

mcp_transport = os.getenv("MCP_TRANSPORT","stdio")

def main():
    if mcp_transport == "stdio":
        mcp.run(
            transport=mcp_transport
        )
    elif mcp_transport == "streamable-http":
        mcp.run(
            transport=mcp_transport,
            mount_path="/mcp"
        )

# from mcp_container import app
if __name__ == "__main__":
    main()