from mcp_container import mcp, app

# from tools.user_profile import collect_basic_profile, collect_household_profile
from tools.check_eligibility import check_eligibility
from tools.required_documents import required_documents

from prompt.required_document import required_document_prompt
from prompt.check_eligibility import initial_onboarding_prompt

# from mcp_container import app
if __name__ == "__main__":
    mcp.run(transport="streamable-http", 
            mount_path="/mcp"
            )