import logging
import os
import re
import threading
from typing import Literal, List, Dict, Any
import torch
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from sentence_transformers import SentenceTransformer

from mcp_container import mcp

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
# Embedding 모델
# -------------------------------------------------
logger.info("📡 Loading Embedding Model...")
model = SentenceTransformer("jhgan/ko-sroberta-multitask")
logger.info("✅ Model loaded")

# -------------------------------------------------
# DB Pool
# -------------------------------------------------
db_pool: AsyncConnectionPool | None = None
_init_lock = threading.Lock()


def init_db_pool():
    global db_pool

    with _init_lock:
        if db_pool is not None:
            return

        db_host = os.getenv("DB_HOST", "postgres")
        db_port = int(os.getenv("DB_PORT", "5432"))
        db_name = os.getenv("DB_NAME")
        db_user = os.getenv("DB_USERNAME")
        db_pass = os.getenv("DB_PASSWORD")

        logger.info(f"🚀 Connecting DB {db_host}:{db_port}")

        db_pool = AsyncConnectionPool(
            conninfo=f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}",
            min_size=1,
            max_size=3,
            open=False,
        )

        logger.info("✅ DB Pool Ready")


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
    if db_pool is None:
        init_db_pool()

    if db_pool.closed:
        await db_pool.open()

    # -----------------------------
    # Query 정리
    # -----------------------------
    cleaned_query = re.sub(r"\d+살|\d+세|\d+", "", query_text).strip()
    if not cleaned_query:
        cleaned_query = query_text

    # -----------------------------
    # embedding → vector 문자열
    # -----------------------------
    embedding = model.encode(cleaned_query).tolist()
    query_embedding = "[" + ",".join(map(str, embedding)) + "]"

    target_keywords = extract_intent_keywords(query_text)

    sido_pattern = f"%{sido[:2]}%" if sido else "%"
    # gender: DB는 A/M/F, tool 파라미터는 ALL/MALE/FEMALE
    gender_db = {"MALE": "M", "FEMALE": "F", "ALL": "A"}.get(gender, "A")

    try:
        sql = """
        SELECT * FROM (
            SELECT
                ws.service_id,
                ws.service_name,
                ws.service_purpose,
                ws.apply_url,

                (1 - (ws.embedding <=> %s::vector))::float AS vector_score,

                (CASE
                    WHEN ws.service_name    LIKE ANY(%s)
                    OR   ws.service_purpose LIKE ANY(%s)
                    THEN 0.5
                    ELSE 0
                END)::float AS intent_bonus,

                (
                    -- 지역 보너스
                    (CASE
                        WHEN wt.sido IS NULL OR wt.sido = '' THEN 0.1
                        WHEN wt.sido ILIKE %s               THEN 0.2
                        ELSE 0
                    END)
                    +
                    -- 성별 보너스
                    (CASE
                        WHEN wt.gender = 'A'  THEN 0.05
                        WHEN wt.gender = %s   THEN 0.1
                        ELSE 0
                    END)
                    +
                    -- 가구 형태 보너스
                    (CASE
                        WHEN %s IS NOT NULL
                         AND wt.household_types @> ARRAY[%s]::TEXT[]
                        THEN 0.15
                        ELSE 0
                    END)
                    +
                    -- 소득 기준 보너스
                    (CASE
                        WHEN %s IS NOT NULL
                         AND wc.income_min_pct <= %s
                         AND wc.income_max_pct >= %s
                        THEN 0.15
                        ELSE 0
                    END)
                )::float AS profile_bonus

            FROM welfare_service   ws
            JOIN welfare_target    wt ON wt.service_id = ws.service_id
            JOIN welfare_criteria  wc ON wc.service_id = ws.service_id

            WHERE
                wt.min_age <= %s
                AND (wt.max_age = 0 OR wt.max_age >= %s)

        ) sub

        ORDER BY (vector_score + intent_bonus + profile_bonus) DESC
        LIMIT 5
        """

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

        return {
            "count": len(services),
            "search_strategy": "Semantic + Intent + Profile",
            "recommended_services": services,
        }

    except Exception as e:
        logger.error(f"❌ Eligibility Error: {e}")
        return {"error": str(e)}