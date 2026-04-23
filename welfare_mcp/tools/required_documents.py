import logging
import threading
import re
from typing import List, Literal, Dict, Any
from mcp_container import mcp

logger = logging.getLogger(__name__)
from backend.DB_Connection import get_db_pool, close_db_pool

# -------------------------------------------------
# DB Connection
# -------------------------------------------------
# db_pool: ConnectionPool | None = None
# _init_lock = threading.Lock()
sem = threading.Semaphore(1)


# -------------------------------------------------
# MCP Tool Definition
# -------------------------------------------------
@mcp.tool(
    name="required_documents",
    description="선택한 서비스 ID의 실제 구비서류 목록을 DB에서 조회합니다.",
)
async def required_documents(
    service_id: str,
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = "ADULT",
    employment_status: Literal[
        "EMPLOYED", "UNEMPLOYED", "STUDENT", "SELF_EMPLOYED", "UNKNOWN"
    ] = "UNKNOWN",
    income_level: Literal[
        "BELOW_MEDIAN_50",
        "MEDIAN_50_100",
        "MEDIAN_100_150",
        "ABOVE_MEDIAN_150",
        "UNKNOWN",
    ] = "UNKNOWN",
) -> Dict[str, Any]:

    # DB 연결 풀 초기화 (최초 1회, 이후 재사용)
    try:
        db_pool = await get_db_pool()
    except Exception as e:
        logger.error(f"❌ DB Pool Error: {e}")
        return {"error": "DB 연결에 실패했습니다."}

    # 쓰레드 안정성을 위해 세마포어로 동시 접근 제어
    with sem:
        try:
            # 사용자님이 확인해주신 정확한 컬럼명 적용
            query = """
                SELECT
                    service_name,
                    required_documents,                       -- 본인 준비 서류
                    official_required_documents,              -- 기관 확인 (공식)
                    personal_verification_required_documents, -- [수정됨] 정확한 컬럼명 반영
                    apply_url
                FROM welfare_service
                WHERE service_id = $1
            """

            # DB에서 서비스 ID에 해당하는 행을 가져옴
            with db_pool.acquire(timeout=10.0) as conn:
                row = conn.fetchrow(query, service_id)

            # 데이터가 아예 없는 경우
            if not row:
                return {
                    "status": "no_data",
                    "message": f"ID {service_id}에 해당하는 서비스가 없습니다.",
                }

            def parse_docs(value: str | None) -> List[str]:
                if not value or value.strip() in (
                    "",
                    "-",
                    "해당없음",
                    "없음",
                    "null",
                    "NULL",
                ):
                    return []
                # 줄바꿈, 쉼표, 세미콜론 등으로 분리
                return [d.strip() for d in re.split(r"[\n,;·]+", value) if d.strip()]

            # 1. 본인 준비 서류
            required_now = parse_docs(row["required_documents"])

            # 2. 공공기관 확인 서류 (공식 + 개인확인 합침)
            # [수정됨] 정확한 키 값 사용
            verified_by_officer = parse_docs(
                row["personal_verification_required_documents"]
            ) + parse_docs(row["official_required_documents"])

            # 3. 조건부 서류 (프로필 기반 로직)
            conditional = []
            if employment_status == "UNEMPLOYED":
                conditional.append("고용보험 미가입 확인서")
            elif employment_status == "EMPLOYED":
                conditional.append("재직증명서")
            elif employment_status == "STUDENT":
                conditional.append("재학증명서")

            if income_level in ("BELOW_MEDIAN_50", "MEDIAN_50_100"):
                conditional.append("소득금액증명원")

            return {
                "service_name": row["service_name"],
                "required_now": list(dict.fromkeys(required_now)),  # 중복 제거
                "verified_by_officer": list(
                    dict.fromkeys(verified_by_officer)
                ),  # 중복 제거
                "conditional_by_profile": conditional,
                "apply_url": row["apply_url"] if row["apply_url"] else "",
                "status": "success",
            }

        except db_pool.UndefinedColumnError as e:
            # 혹시라도 또 오타가 있을 경우를 대비한 로그
            logger.error(f"❌ Column Name Error: {e}")
            return {"error": f"DB 컬럼명 불일치: {str(e)}"}

        except db_pool.TimeoutError:
            return {
                "error": "요청이 너무 많아 지연되고 있습니다. 잠시 후 다시 시도해주세요."
            }

        except Exception as e:
            logger.error(f"❌ Required Documents Error: {e}")
            return {"error": f"조회 중 오류 발생: {str(e)}"}
        
    try:
        await close_db_pool()
    except Exception as e:
        logger.error(f"❌ DB Pool Close Error: {e}")    
