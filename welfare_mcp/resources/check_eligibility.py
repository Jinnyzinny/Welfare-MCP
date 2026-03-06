from mcp_container import mcp
from psycopg import sql
import logging
from dotenv import load_dotenv

load_dotenv()

@mcp.resource()
async def check_eligibility(
    query_text: str,
    age: int
) -> dict:
    """
    사용자가 상태를 입력하고 사용자의 상태에서 가장 관련성 높은 복지 제도를 추천합니다. 입력된 질문에서 나이, 성별, 지역 정보를 추출하여 가장 관련성 높은 서비스를 반환합니다.
    """  

    db_pool = mcp.get_resource("db_pool")
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT support_target FROM welfare_service")
            services = await cur.fetchall()
            