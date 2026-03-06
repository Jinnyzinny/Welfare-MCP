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
    description="사용자의 나이, 성별, 지역, 질문을 기반으로 복지 서비스를 추천합니다",
)
async def check_eligibility(
    query_text: str,
    age: int,
    gender: Literal["MALE", "FEMALE", "ALL"] = "ALL",
    sido: str | None = None,
    sigungu: str | None = None,
) -> Dict[str, Any]:

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

    try:

        sql = """
        SELECT * FROM (
            SELECT 
                service_id,
                service_name,
                service_purpose,
                apply_url,

                (1 - (embedding <=> %s::vector))::float AS vector_score,

                (CASE
                    WHEN service_name LIKE ANY(%s)
                    OR service_purpose LIKE ANY(%s)
                    THEN 0.5
                    ELSE 0
                END)::float AS intent_bonus,

                (
                    (CASE
                        WHEN sido IS NULL OR sido = '' THEN 0.1
                        WHEN sido ILIKE %s THEN 0.2
                        ELSE 0
                    END)
                    +
                    (CASE
                        WHEN gender IS NULL OR gender = 'ALL' THEN 0.05
                        WHEN gender = %s THEN 0.1
                        ELSE 0
                    END)
                )::float AS profile_bonus

            FROM welfare_service

            WHERE
                COALESCE(min_age,0) <= %s
                AND (max_age IS NULL OR max_age = 0 OR max_age >= %s)

        ) sub

        ORDER BY (vector_score + intent_bonus + profile_bonus) DESC
        LIMIT 5
        """

        async with db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:

                await cur.execute(
                    sql,
                    (
                        query_embedding,
                        target_keywords,
                        target_keywords,
                        sido_pattern,
                        gender,
                        age,
                        age,
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
                        r["vector_score"]
                        + r["intent_bonus"]
                        + r["profile_bonus"],
                        2,
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