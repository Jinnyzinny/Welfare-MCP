import logging
import os
import atexit
from typing import List, Literal, Dict, Any

import asyncpg
from mcp_container import mcp
from backend.entity.UserProfile import UserProfile

# -------------------------------------------------
# Logging
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# Async DB Pool (Global)
# -------------------------------------------------
db_pool: asyncpg.Pool | None = None


async def init_db_pool():
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
        logger.info("✅ Async DB Pool initialized.")
    except Exception:
        logger.exception("❌ Failed to initialize DB pool")
        raise


@atexit.register
def close_db_pool():
    if db_pool:
        try:
            import asyncio
            asyncio.run(db_pool.close())
        except Exception:
            pass


# -------------------------------------------------
# MCP Tool
# -------------------------------------------------
@mcp.tool(
    name="required_documents",
    description="선택한 서비스 ID와 사용자 프로필을 기반으로 구비서류 목록을 조회합니다."
)
async def required_documents(
    service_id: str,
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = "ADULT",
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
    ] = "UNKNOWN",
    household_type: Literal[
        "SINGLE",
        "PARENT_CHILD",
        "COUPLE",
        "SINGLE_PARENT",
        "OTHER"
    ] = "OTHER"
) -> Dict[str, Any]:
    """
    서비스별 공통 서류 + 사용자 조건부 서류 반환
    """

    if not db_pool:
        await init_db_pool()

    # -------------------------------------------------
    # User Profile
    # -------------------------------------------------
    profile = UserProfile(
        age_group=age_group,
        income_level=income_level,
        employment_status=employment_status,
        household_type=household_type
    )

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
            return {
                "error": f"서비스 ID '{service_id}'를 DB에서 찾을 수 없습니다."
            }

        # -------------------------------------------------
        # Document parsing
        # -------------------------------------------------
        def parse_docs(value: str | None) -> List[str]:
            if not value:
                return []
            return [
                d.strip()
                for d in value.replace("\n", ",").split(",")
                if d.strip()
            ]

        required_now = (
            parse_docs(row["required_documents"])
            + parse_docs(row["self_documents"])
        )

        verified_by_officer = parse_docs(
            row["personal_verification_documents"]
        )

        conditional: List[str] = []

        # -------------------------------------------------
        # Conditional documents (User-based)
        # -------------------------------------------------
        if profile.employment_status == "UNEMPLOYED":
            conditional.append("고용보험 미가입 확인서")
        elif profile.employment_status == "EMPLOYED":
            conditional.append("근로소득 원천징수영수증")
        elif profile.employment_status == "STUDENT":
            conditional.append("재학증명서")

        if profile.income_level in (
            "BELOW_MEDIAN_50",
            "MEDIAN_50_100"
        ):
            conditional.append("소득금액증명원")

        # -------------------------------------------------
        # Deduplication & Return
        # -------------------------------------------------
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

    except Exception:
        logger.exception("❌ Error in required_documents")
        return {
            "error": "데이터베이스 조회 중 오류가 발생했습니다."
        }
