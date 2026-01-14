from mcp_container import mcp
from mcp.server.fastmcp.prompts import base

@mcp.prompt(name="사용자 프로필 확보")
async def get_user_profile_prompt() -> str:
    return [
        base.UserMessage(content="사용자의 연령, 거주지, 가족 구성원 수, 소득 수준 등의 프로필 정보를 확보하세요."),
        base.SystemMessage(content="사용자에게 필요한 정보를 질문하여 프로필을 완성하세요."),
    ]