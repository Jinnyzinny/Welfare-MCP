import logging
import os
import asyncio
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
_init_lock = asyncio.Lock() # 동시 초기화 방지

async def init_db_pool():
    global db_pool
    
    # 락을 사용하여 여러 요청이 동시에 초기화를 시도하는 것 방지
    async with _init_lock:
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
                max_size=5, # Lightsail 사양을 고려해 10에서 5로 축소
                command_timeout=10,
                connect_timeout=10 # 접속 시도 자체에 타임아웃 부여 (폭주 방지 핵심)
            )
            logger.info("✅ Async DB Pool initialized successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to initialize DB pool: {e}")
            # 에러 발생 시 예외를 던져서 도구 호출 자체를 실패 처리 (무한 루프 방지)
            raise e

# -------------------------------------------------
# MCP Tool
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 나이와 가구 형태를 기준으로 신청 가능한 복지 서비스를 검색합니다."
)
async def check_eligibility(
    age: int,
    household_type: Literal["SINGLE", "PARENT_CHILD", "COUPLE", "SINGLE_PARENT", "OTHER"] | None = None,
    income_level: Literal["BELOW_MEDIAN_50", "MEDIAN_50_100", "MEDIAN_100_150", "ABOVE_MEDIAN_150", "UNKNOWN"] = "UNKNOWN",
    employment_status: Literal["EMPLOYED", "UNEMPLOYED", "STUDENT", "SELF_EMPLOYED", "UNKNOWN"] = "UNKNOWN"
) -> Dict[str, Any]:
    
    # 풀이 없으면 초기화 시도
    if not db_pool:
        try:
            await init_db_pool()
        except Exception:
            return {"error": "데이터베이스 연결에 실패했습니다. 관리자에게 문의하세요."}

    household_map = {
        "SINGLE": "1인",
        "SINGLE_PARENT": "한부모",
        "COUPLE": "부부",
        "PARENT_CHILD": "다자녀",
        "OTHER": ""
    }
    keyword = household_map.get(household_type, "")
    household_pattern = f"%{keyword}%" if keyword else "%"

    # SQL 쿼리 (row["apply_url"] 참조를 위해 명시적 포함 완료)
    query = """
        SELECT
            service_id, service_name, service_purpose, support_target, apply_url
        FROM welfare_service
        WHERE
            min_age <= $1 AND max_age >= $1
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

        services = [
            {
                "service_id": row["service_id"],
                "name": row["service_name"],
                "purpose": row["service_purpose"],
                "target_text": row["support_target"],
                "url": row["apply_url"] if row["apply_url"] else ""
            }
            for row in rows
        ]

        return {
            "count": len(services),
            "recommended_services": services
        }

    except Exception as e:
        logger.exception("❌ DB Query Error")
        return {"error": "데이터 조회 중 오류가 발생했습니다."}

# -------------------------------------------------
# MCP Lifecycle (atexit 대신 권장 방식)
# -------------------------------------------------
@mcp.on_shutdown
async def on_shutdown():
    global db_pool
    if db_pool:
        logger.info("🔻 Closing DB Pool...")
        await db_pool.close()