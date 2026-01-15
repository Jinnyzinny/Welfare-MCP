import logging
import asyncio
import os
import re
from typing import List, Literal, Dict, Any
import asyncpg
from mcp_container import mcp

logger = logging.getLogger(__name__)

# -------------------------------------------------
# DB Connection (독립적 실행을 위해 내장)
# -------------------------------------------------
db_pool: asyncpg.Pool | None = None
_init_lock = asyncio.Lock()
sem = asyncio.Semaphore(1) # 동시 실행 제한

async def init_db_pool():
    global db_pool
    async with _init_lock:
        if db_pool is not None: return
        try:
            # 환경 변수 로드
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = int(os.getenv("DB_PORT", "5432"))
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USERNAME")
            db_pass = os.getenv("DB_PASSWORD")

            # 2GB RAM 환경 최적화 설정
            db_pool = await asyncpg.create_pool(
                host=db_host, port=db_port, database=db_name,
                user=db_user, password=db_pass,
                min_size=1, max_size=2, timeout=10.0
            )
            logger.info("✅ DB Pool initialized in required_documents.")
        except Exception as e:
            logger.error(f"❌ DB Init Error: {e}")
            raise

# -------------------------------------------------
# MCP Tool Definition
# -------------------------------------------------
@mcp.tool(
    name="required_documents",
    description="선택한 서비스 ID의 실제 구비서류 목록을 DB에서 조회합니다."
)
async def required_documents(
    service_id: str,
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = "ADULT",
    employment_status: Literal["EMPLOYED", "UNEMPLOYED", "STUDENT", "SELF_EMPLOYED", "UNKNOWN"] = "UNKNOWN",
    income_level: Literal["BELOW_MEDIAN_50", "MEDIAN_50_100", "MEDIAN_100_150", "ABOVE_MEDIAN_150", "UNKNOWN"] = "UNKNOWN"
) -> Dict[str, Any]:
    
    # DB Pool 초기화 체크
    if db_pool is None: await init_db_pool()

    async with sem:
        try:
            query = """
                SELECT
                    service_name,
                    required_documents,           -- 필수 구비 서류
                    official_required_documents,  -- 공식 확인 서류
                    personal_verification_documents, -- 공무원 확인 서류
                    apply_url
                FROM welfare_service
                WHERE service_id = $1
            """

            async with db_pool.acquire(timeout=10.0) as conn:
                row = await conn.fetchrow(query, service_id)

            if not row:
                return {"status": "no_data", "message": f"ID {service_id}에 대한 서류 정보가 없습니다."}

            def parse_docs(value: str | None) -> List[str]:
                if not value or value.strip() in ("", "-", "해당없음", "없음"): return []
                return [d.strip() for d in re.split(r'[\n,;·]+', value) if d.strip()]

            required_now = parse_docs(row["required_documents"])
            verified_by_officer = parse_docs(row["personal_verification_documents"]) + parse_docs(row["official_required_documents"])
            
            # 조건부 서류 추가 로직
            conditional = []
            if employment_status == "UNEMPLOYED":
                conditional.append("고용보험 미가입 확인서")
            elif employment_status == "EMPLOYED":
                conditional.append("재직증명서")
                
            if income_level in ("BELOW_MEDIAN_50", "MEDIAN_50_100"):
                conditional.append("소득금액증명원")

            return {
                "service_name": row["service_name"],
                "required_now": list(dict.fromkeys(required_now)),
                "verified_by_officer": list(dict.fromkeys(verified_by_officer)),
                "conditional_by_profile": conditional,
                "apply_url": row["apply_url"] if row["apply_url"] else "",
                "notes": ["관할 기관 유선 확인 시 가장 정확합니다."]
            }

        except asyncio.TimeoutError:
            return {"error": "요청이 너무 많아 지연되고 있습니다. 잠시 후 다시 시도해주세요."}
        except Exception as e:
            logger.error(f"❌ Required Documents Error: {e}")
            return {"error": f"조회 중 오류 발생: {str(e)}"}