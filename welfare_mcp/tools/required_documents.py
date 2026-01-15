import logging
import os
import asyncio
import re
from typing import List, Literal, Dict, Any

import asyncpg
from mcp_container import mcp
from backend.entity.UserProfile import UserProfile

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db_pool: asyncpg.Pool | None = None
_init_lock = asyncio.Lock()

async def init_db_pool():
    global db_pool
    async with _init_lock:
        if db_pool is not None:
            return
        try:
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = int(os.getenv("DB_PORT", "5432"))
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USERNAME")
            db_pass = os.getenv("DB_PASSWORD")

            logger.info(f"🚀 Connecting to DB: {db_host}:{db_port}")

            db_pool = await asyncpg.create_pool(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_pass,
                min_size=1,
                max_size=3,
                timeout=5.0
            )
            logger.info("✅ Async DB Pool initialized successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to initialize DB pool: {e}")
            raise

@mcp.tool(
    name="required_documents",
    description="선택한 서비스 ID와 사용자 프로필을 기반으로 실제 DB에 저장된 구비서류 목록을 조회합니다."
)
async def required_documents(
    service_id: str,
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = "ADULT",
    income_level: Literal["BELOW_MEDIAN_50", "MEDIAN_50_100", "MEDIAN_100_150", "ABOVE_MEDIAN_150", "UNKNOWN"] = "UNKNOWN",
    employment_status: Literal["EMPLOYED", "UNEMPLOYED", "STUDENT", "SELF_EMPLOYED", "UNKNOWN"] = "UNKNOWN",
    household_type: Literal["SINGLE", "PARENT_CHILD", "COUPLE", "SINGLE_PARENT", "OTHER"] = "OTHER"
) -> Dict[str, Any]:
    
    if db_pool is None:
        await init_db_pool()

    profile = UserProfile(
        age_group=age_group,
        income_level=income_level,
        employment_status=employment_status,
        household_type=household_type
    )

    query = """
        SELECT
            service_id,
            service_name,
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
            return {"error": f"ID '{service_id}'에 해당하는 서비스 정보를 찾을 수 없습니다."}

        # [개선] 더 정교한 문서 파싱 로직
        def parse_docs(value: str | None) -> List[str]:
            if not value or value.strip() in ("", "-", "해당없음", "없음"): 
                return []
            # 줄바꿈, 쉼표, 세미콜론, 가운뎃점 등으로 분리
            docs = re.split(r'[\n,;·\t]+', value)
            # 공백 제거 및 중복/짧은 단어 필터링
            return [d.strip() for d in docs if d.strip() and len(d.strip()) > 1]

        # 기본 서류 합치기
        required_now = parse_docs(row["required_documents"]) + parse_docs(row["self_documents"])
        # 공무원 확인 서류 (행정정보 공동이용)
        verified_by_officer = parse_docs(row["personal_verification_documents"]) + parse_docs(row["official_required_documents"])
        
        conditional: List[str] = []

        # --- 사용자 프로필 기반 조건부 서류 추가 (Business Logic) ---
        if profile.employment_status == "UNEMPLOYED":
            conditional.append("고용보험 피보험자격 이력내역서(실업 증빙용)")
        elif profile.employment_status == "EMPLOYED":
            conditional.append("재직증명서 또는 근로계약서")
        elif profile.employment_status == "STUDENT":
            conditional.append("재학증명서 또는 휴학증명서")

        if profile.income_level in ("BELOW_MEDIAN_50", "MEDIAN_50_100"):
            conditional.append("차상위계층 확인서 또는 기초생활수급자 증명서")

        # 결과 반환 (중복 제거)
        return {
            "service_name": row["service_name"],
            "required_now": list(dict.fromkeys(required_now)),
            "conditional_by_profile": list(dict.fromkeys(conditional)),
            "verified_by_officer": list(dict.fromkeys(verified_by_officer)),
            "notes": [
                "위 목록은 DB에 등록된 공식 데이터를 바탕으로 합니다.",
                "지자체 및 접수 시점에 따라 추가 서류가 발생할 수 있으니 방문 전 유선 확인을 권장합니다."
            ]
        }
    except Exception as e:
        logger.error(f"❌ Error in required_documents: {str(e)}", exc_info=True)
        return {"error": "서류 정보를 가져오는 중 시스템 오류가 발생했습니다."}