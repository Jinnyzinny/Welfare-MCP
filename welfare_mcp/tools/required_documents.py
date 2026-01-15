import logging
import os
import asyncio
from typing import List, Literal, Dict, Any

import asyncpg
from mcp_container import mcp
from backend.entity.UserProfile import UserProfile

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# Async DB Pool (Global) 및 상태 관리
# -------------------------------------------------
db_pool: asyncpg.Pool | None = None
_init_lock = asyncio.Lock()  # 동시 초기화 방지

async def init_db_pool():
    global db_pool
    
    async with _init_lock:
        if db_pool is not None:
            return

        try:
            # .env 값 읽기
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = int(os.getenv("DB_PORT", "5432"))
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USERNAME")
            db_pass = os.getenv("DB_PASSWORD")

            logger.info(f"Connecting to DB at {db_host}:{db_port}...")

            db_pool = await asyncpg.create_pool(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_pass,
                min_size=1,
                max_size=5,        # Lightsail 부하 방지를 위해 축소
                command_timeout=10, # 쿼리 타임아웃
                connect_timeout=10  # 접속 타임아웃 (폭주 방지 핵심)
            )
            logger.info("✅ Async DB Pool initialized successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to initialize DB pool: {e}")
            # 에러 발생 시 여기서 멈춤 (무한 루프 방지)
            raise

# -------------------------------------------------
# MCP Tool: required_documents
# -------------------------------------------------
@mcp.tool(
    name="required_documents",
    description="선택한 서비스 ID와 사용자 프로필을 기반으로 구비서류 목록을 조회합니다."
)
async def required_documents(
    service_id: str,
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = "ADULT",
    income_level: Literal["BELOW_MEDIAN_50", "MEDIAN_50_100", "MEDIAN_100_150", "ABOVE_MEDIAN_150", "UNKNOWN"] = "UNKNOWN",
    employment_status: Literal["EMPLOYED", "UNEMPLOYED", "STUDENT", "SELF_EMPLOYED", "UNKNOWN"] = "UNKNOWN",
    household_type: Literal["SINGLE", "PARENT_CHILD", "COUPLE", "SINGLE_PARENT", "OTHER"] = "OTHER"
) -> Dict[str, Any]:
    
    # 1. 풀 초기화 확인 (에러 시 예외 전파되어 AI 재시도 억제)
    if db_pool is None:
        await init_db_pool()

    profile = UserProfile(
        age_group=age_group,
        income_level=income_level,
        employment_status=employment_status,
        household_type=household_type
    )

    # apply_url이 누락되어 에러나던 부분 보완 (쿼리에 포함 확인 필요)
    query = """
        SELECT
            service_id,
            required_documents,
            official_required_documents,
            personal_verification_documents,
            self_documents
        FROM welfare_service
        WHERE service_id = $1
    """

    try:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(query, service_id)

        if not row:
            return {"error": f"서비스 ID '{service_id}'를 찾을 수 없습니다."}

        # 문서 파싱 함수
        def parse_docs(value: str | None) -> List[str]:
            if not value: return []
            return [d.strip() for d in value.replace("\n", ",").split(",") if d.strip()]

        required_now = parse_docs(row["required_documents"]) + parse_docs(row["self_documents"])
        verified_by_officer = parse_docs(row["personal_verification_documents"])
        conditional: List[str] = []

        # 조건부 서류 로직
        if profile.employment_status == "UNEMPLOYED":
            conditional.append("고용보험 미가입 확인서")
        elif profile.employment_status == "EMPLOYED":
            conditional.append("근로소득 원천징수영수증")
        elif profile.employment_status == "STUDENT":
            conditional.append("재학증명서")

        if profile.income_level in ("BELOW_MEDIAN_50", "MEDIAN_50_100"):
            conditional.append("소득금액증명원")

        return {
            "service_id": service_id,
            "required_now": list(dict.fromkeys(required_now)),
            "conditional": list(dict.fromkeys(conditional)),
            "verified_by_officer": list(dict.fromkeys(verified_by_officer)),
            "notes": [
                "정확한 서류는 접수기관에서 최종 확인이 필요합니다.",
                "공무원 확인 서류는 행정정보 공동이용으로 대체될 수 있습니다."
            ]
        }
    except Exception as e:
        logger.error(f"❌ Error in required_documents: {e}")
        return {"error": "데이터 조회 중 서버 오류가 발생했습니다."}

# -------------------------------------------------
# 안전한 종료 처리 (atexit 대신 MCP 공식 지원 방법 권장)
# -------------------------------------------------
# @mcp.on_shutdown
# async def on_shutdown():
#     global db_pool
#     if db_pool:
#         await db_pool.close()
#         logger.info("👋 DB Pool closed.")