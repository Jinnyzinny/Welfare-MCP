import logging
import os
from typing import Literal, List, Dict, Any
import re
import torch
from psycopg.rows import dict_row
from sentence_transformers import SentenceTransformer

from mcp_container import mcp
from backend.repository.check_eligibility import score_eligibility_query

# -------------------------------------------------
# Thread 제한
# -------------------------------------------------
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
torch.set_num_threads(1)

# -------------------------------------------------
# 로깅
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------
# Embedding 모델 (결국 벡터 임베딩이 되더라도 MCP 서버에서는 그걸 해석해야 함)
# -------------------------------------------------
logger.info("📡 Loading Embedding Model...")
model = SentenceTransformer("jhgan/ko-sroberta-multitask")
logger.info("✅ Model loaded")

# -------------------------------------------------
# DB Pool
# -------------------------------------------------
from backend.DB_Connection import get_db_pool, close_db_pool

# -------------------------------------------------
# Intent Keyword 추출
# -------------------------------------------------
def extract_intent_keywords(query: str) -> List[str]:

    intent_map = {
        "job": {
            "triggers": ["취업", "일자리", "구직", "채용", "근로"],
            "keywords": ["%취업%", "%일자리%", "%구직%", "%채용%", "%근로%"],
        },
        "startup": {
            "triggers": ["창업", "스타트업", "사업"],
            "keywords": ["%창업%", "%사업%", "%스타트업%"],
        },
        "housing": {
            "triggers": ["주거", "전세", "월세", "임대"],
            "keywords": ["%주거%", "%전세%", "%임대%", "%주택%"],
        },
        "finance": {
            "triggers": ["대출", "금융", "지원금"],
            "keywords": ["%대출%", "%금융%", "%지원금%"],
        },
    }

    found = []

    for _, data in intent_map.items():
        if any(t in query for t in data["triggers"]):
            found.extend(data["keywords"])

    if not found:
        words = query.split()
        found = [f"%{w}%" for w in words if len(w) >= 2]

    return list(set(found))


# -------------------------------------------------
# MCP TOOL
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="쿼리 텍스트 원문, 사용자의 나이, 성별, 지역, 가구형태, 소득수준, 고용 상태, 특별 조건 질문을 기반으로 복지 서비스를 추천합니다",
)
async def check_eligibility(
    query_text: str,
    age: int,
    gender: Literal["M", "F", "A"] = "A",
    sido: str | None = None,
    sigungu: str | None = None,
    household_type: str | None = None,
    income_pct: int | None = None,
    employment_statuses: List[str] | None = None,
    special_condition: List[str] | None = None,
) -> Dict[str, Any]:
    """
    Args:
        query_text    : 자연어 검색어
        age           : 사용자 나이
        gender        : M | F | A
        sido          : 시/도 (예: 서울특별시)
        sigungu       : 시/군/구 (예: 강남구)
        household_type: 가구 형태 태그 (예: 한부모, 다자녀, 1인가구)
        income_pct    : 중위소득 % (예: 50 → 중위소득 50%)
        employment_statuses: 고용 상태 태그 (예: 고용, 실업, 재직)
        special_condition: 특별 조건 태그 (예: 장애인, 아동)
    """
    # mcp 함수 호출 시점 로그
    logger.info(f"[INFO] check_eligibility function called")

    # DB 연결 풀 초기화 (최초 1회, 이후 재사용)
    try:
        db_pool = await get_db_pool()

    except Exception as e:
        logger.error(f"❌ [ERROR] DB Pool Error: {e}")
        return {"error": "DB 연결에 실패했습니다."}

    logger.info(f"[INFO] DB Pool acquired successfully")

    # # -----------------------------
    # # Query 정리
    # # -----------------------------
    cleaned_query = re.sub(r"\d+살|\d+세|\d+", "", query_text).strip()
    if not cleaned_query:
        cleaned_query = query_text

    # # -----------------------------
    # # embedding → vector 문자열
    # # -----------------------------
    embedding = model.encode(cleaned_query).tolist()
    query_embedding = "[" + ",".join(map(str, embedding)) + "]"

    target_keywords = extract_intent_keywords(query_text)

    sido_pattern = f"%{sido[:2]}%" if sido else "%"
    # gender: DB는 A/M/F, tool 파라미터는 ALL/MALE/FEMALE
    gender_db = {"MALE": "M", "FEMALE": "F", "ALL": "A"}.get(gender, "A")

    try:
        # # -----------------------------
        # 쿼리 실행문이 너무 길어서 파일 별도 분리 -> backend/repository/check_eligibility.py
        # 향후 alchemy 같은 ORM 도입 시 repository 레이어에서 쿼리 관리하는 형태로 수정 가능
        # # -----------------------------
        sql = score_eligibility_query()
        async with db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    sql,
                    (
                        # ── 외부 SELECT 점수 계산 ──
                        sido,             # 4. wt.sido ILIKE %s (지역 보너스)
                        gender,                # 5. wt.gender = %s (성별 보너스)
                        household_type,           # 6. household_types @> ARRAY[%s] (가구 보너스)
                        income_pct,               # 7. income_min_pct <= %s (소득 보너스)
                        income_pct,               # 8. income_max_pct >= %s (소득 보너스)

                        # ── 내부 서브쿼리 필터링 ──
                        income_pct,               # 9.  income_min_pct <= %s
                        income_pct,               # 10. income_max_pct >= %s
                        age,                      # 11. min_age <= %s
                        age,                      # 12. max_age >= %s
                        gender,                # 13. gender IN ('A', %s)
                        sido,             # 14. sido ILIKE %s
                        sigungu,          # 15. sigungu ILIKE %s
                        household_type,           # 16. household_types @> ARRAY[%s]
                        employment_statuses,        # 17. employment_statuses @> ARRAY[%s]
                        special_condition,        # 18. special_conditions @> ARRAY[%s]
                    ),
                )
                rows = await cur.fetchall()

                # 조건에 맞는 서비스가 없는 경우 예외 처리
                if rows is None:
                    logger.info(f"[INFO] 해당 조건에 맞는 서비스를 찾을 수 없습니다.")
                    raise ValueError("조건에 맞는 서비스를 찾을 수 없습니다.")
                
        # 서비스별 점수 계산 및 정렬
        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "url": r["apply_url"] or "",
                "score_breakdown": {
                    "vector": round(r["vector_score"], 2),
                    "intent": round(r["intent_bonus"], 2),
                    "profile": round(r["profile_bonus"], 2),
                    "total": round(
                        r["vector_score"] + r["intent_bonus"] + r["profile_bonus"], 2
                    ),
                },
            }
            for r in rows
        ]

        # 최종 결과 반환
        return {
            "count": len(services),
            "search_strategy": "Semantic + Intent + Profile",
            "recommended_services": services,
        }
    
    except Exception as e:
        logger.error(f"❌ Eligibility Error: {e}")
        return {"error": str(e)}

    finally:
        await close_db_pool()