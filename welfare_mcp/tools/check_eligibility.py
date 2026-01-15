import logging
import os
import asyncio
from typing import Literal, List, Dict, Any

import asyncpg
from mcp_container import mcp

# 만약 UserProfile 엔티티가 별도 파일에 있다면 임포트 유지
# from backend.entity.UserProfile import UserProfile

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# DB Pool 전역 관리 및 초기화 락
# -------------------------------------------------
db_pool: asyncpg.Pool | None = None
_init_lock = asyncio.Lock()

async def init_db_pool():
    global db_pool
    
    async with _init_lock:
        if db_pool is not None:
            return

        try:
            # 환경 변수 로드 및 타입 변환 (중요!)
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = int(os.getenv("DB_PORT", "5432")) # 정수 변환 확인
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USERNAME")
            db_pass = os.getenv("DB_PASSWORD")

            logger.info(f"🚀 Connecting to DB: {db_host}:{db_port} (Timeout: 5s)")

            db_pool = await asyncpg.create_pool(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_pass,
                min_size=1,
                max_size=3,        # Lightsail 메모리 부족 방지
                connect_timeout=5,  # 접속 타임아웃 설정
                command_timeout=10  # 쿼리 타임아웃 설정
            )
            logger.info("✅ Async DB Pool initialized successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to initialize DB pool: {e}")
            raise 

# -------------------------------------------------
# MCP Tool: check_eligibility
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 나이와 가구 형태를 기준으로 신청 가능한 복지 서비스를 검색합니다."
)
async def check_eligibility(
    age: int,
    household_type: Literal["SINGLE", "PARENT_CHILD", "COUPLE", "SINGLE_PARENT", "OTHER"] | None = None
) -> Dict[str, Any]:
    
    if db_pool is None:
        try:
            await init_db_pool()
        except Exception:
            return {"error": "DB 연결에 실패했습니다. 잠시 후 다시 시도해 주세요."}

    # 가구 형태 검색용 패턴 (household_type이 있을 경우만 처리)
    household_map = {
        "SINGLE": "1인",
        "SINGLE_PARENT": "한부모",
        "COUPLE": "부부",
        "PARENT_CHILD": "다자녀"
    }
    keyword = household_map.get(household_type, "")
    pattern = f"%{keyword}%" if keyword else "%"

    # 쿼리: age와 household_type을 모두 고려
    query = """
        SELECT service_id, service_name, service_purpose, support_target, apply_url
        FROM welfare_service
        WHERE min_age <= $1 AND max_age >= $1
          AND (household_type IS NULL OR household_type LIKE $2)
        LIMIT 5
    """

    try:
        async with db_pool.acquire() as conn:
            # 파라미터 2개($1, $2)를 정확히 전달
            rows = await conn.fetch(query, age, pattern)
            
        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "url": r["apply_url"] if r["apply_url"] else ""
            } for r in rows
        ]
        
        return {
            "count": len(services),
            "recommended_services": services
        }
    except Exception as e:
        logger.error(f"❌ DB Query Error: {e}")
        return {"error": "데이터 조회 중 오류가 발생했습니다."}

# @mcp.
# async def shutdown():
#     global db_pool
#     if db_pool:
#         await db_pool.close()
#         logger.info("👋 Database Pool closed.")