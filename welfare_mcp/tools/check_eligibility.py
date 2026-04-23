import logging
import os
from typing import Literal, List, Dict, Any
import re
import torch
from psycopg.rows import dict_row
from sentence_transformers import SentenceTransformer

from mcp_container import mcp
from welfare_mcp.backend.repository.check_eligibility import check_eligibility_query

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
from welfare_mcp.backend.DB_Connection import get_db_pool, close_db_pool

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
    description="사용자의 나이, 성별, 지역, 가구형태, 소득수준, 질문을 기반으로 복지 서비스를 추천합니다",
)
async def check_eligibility(
    query_text: str,
    age: int,
    gender: Literal["MALE", "FEMALE", "ALL"] = "ALL",
    sido: str | None = None,
    sigungu: str | None = None,
    household_type: str | None = None,
    income_pct: int | None = None,
) -> Dict[str, Any]:
    """
    Args:
        query_text    : 자연어 검색어
        age           : 사용자 나이
        gender        : MALE | FEMALE | ALL
        sido          : 시/도 (예: 서울특별시)
        sigungu       : 시/군/구 (예: 강남구)
        household_type: 가구 형태 태그 (예: 한부모, 다자녀, 1인가구)
        income_pct    : 중위소득 % (예: 50 → 중위소득 50%)
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
        sql = check_eligibility_query()
        async with db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    sql,
                    (
                        query_embedding,            # embedding
                        target_keywords,            # service_name LIKE ANY
                        target_keywords,            # service_purpose LIKE ANY
                        sido_pattern,               # sido ILIKE
                        gender_db,                  # gender =
                        household_type,             # 가구 형태 보너스 조건 체크
                        household_type,             # household_types @> ARRAY[?]
                        income_pct,                 # 소득 보너스 조건 체크
                        income_pct if income_pct else 0,   # income_min_pct <=
                        income_pct if income_pct else 999, # income_max_pct >=
                        age,                        # min_age <=
                        age,                        # max_age >=
                    ),
                )
                rows = await cur.fetchall()
                
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
    
    # 예외 처리 (쿼리 실행, 데이터 처리 등에서 발생할 수 있는 모든 예외를 포괄)
    except Exception as e:
        logger.error(f"❌ Eligibility Error: {e}")
        return {"error": str(e)}

    finally:
        await close_db_pool()