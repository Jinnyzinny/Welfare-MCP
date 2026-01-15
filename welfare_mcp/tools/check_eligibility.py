import logging
import os
import atexit
from typing import Literal, List, Dict, Any

import asyncpg
from mcp_container import mcp

# -------------------------------------------------
# Logging
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# Async DB Pool (Global, Singleton)
# -------------------------------------------------
db_pool: asyncpg.Pool | None = None


async def init_db_pool():
    """
    MCP 서버 시작 시 1회 호출
    """
    global db_pool
    if db_pool:
        return

    try:
        db_pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            min_size=1,
            max_size=10,
            command_timeout=5
        )
        logger.info("✅ Async DB Pool initialized successfully.")
    except Exception as e:
        logger.exception("❌ Failed to initialize DB pool")
        raise e


@atexit.register
def close_db_pool():
    """
    프로세스 종료 시 커넥션 정리
    """
    if db_pool:
        logger.info("🔻 Closing DB Pool...")
        try:
            import asyncio
            asyncio.run(db_pool.close())
        except Exception:
            pass


# -------------------------------------------------
# MCP Tool
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 나이와 가구 형태를 기준으로 신청 가능한 복지 서비스를 검색합니다."
)
async def check_eligibility(
    age: int,
    household_type: Literal[
        "SINGLE",
        "PARENT_CHILD",
        "COUPLE",
        "SINGLE_PARENT",
        "OTHER"
    ] | None = None,
    income_level: Literal[
        "BELOW_MEDIAN_50",
        "MEDIAN_50_100",
        "MEDIAN_100_150",
        "ABOVE_MEDIAN_150",
        "UNKNOWN"
    ] = "UNKNOWN",
    employment_status: Literal[
        "EMPLOYED",
        "UNEMPLOYED",
        "STUDENT",
        "SELF_EMPLOYED",
        "UNKNOWN"
    ] = "UNKNOWN"
) -> Dict[str, Any]:
    """
    DB 조건 기반 신청 가능 복지 서비스 추천
    """

    if not db_pool:
        await init_db_pool()

    # 가구 형태 매핑 (Enum → DB 키워드)
    household_map = {
        "SINGLE": "1인",
        "SINGLE_PARENT": "한부모",
        "COUPLE": "부부",
        "PARENT_CHILD": "다자녀",
        "OTHER": ""
    }
    keyword = household_map.get(household_type, "")
    household_pattern = f"%{keyword}%" if keyword else "%"

    query = """
        SELECT
            service_id,
            service_name,
            service_purpose,
            support_target,
            apply_url
        FROM welfare_service
        WHERE
            min_age <= $1
            AND max_age >= $1
            AND (household_type IS NULL OR household_type LIKE $2)
        ORDER BY
            CASE
                WHEN service_name LIKE '%청년%' THEN 1
                WHEN service_name LIKE '%취업%' THEN 2
                ELSE 3
            END,
            service_id DESC
        LIMIT 5
    """

    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, age, household_pattern)

        services: List[Dict[str, Any]] = [
            {
                "service_id": row["service_id"],
                "name": row["service_name"],
                "purpose": row["service_purpose"],
                "target_text": row["support_target"],
                "url": row["apply_url"]
            }
            for row in rows
        ]

        return {
            "count": len(services),
            "recommended_services": services
        }

    except Exception as e:
        logger.exception("❌ DB Query Error")
        return {
            "error": "데이터 조회 중 오류가 발생했습니다."
        }
