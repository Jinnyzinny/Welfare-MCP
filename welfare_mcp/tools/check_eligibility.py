import logging
import os
import re
from psycopg_pool import ConnectionPool
import threading
from typing import Literal, List, Dict, Any

from mcp_container import mcp
from sentence_transformers import SentenceTransformer

import os
import torch

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
torch.set_num_threads(1)

# -------------------------------------------------
# 1. 설정 및 모델 로드
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("📡 Loading Embedding Model (jhgan/ko-sroberta-multitask)...")
model = SentenceTransformer("jhgan/ko-sroberta-multitask")
logger.info("✅ Model loaded successfully.")

# -------------------------------------------------
# 2. DB Pool
# -------------------------------------------------
db_pool: ConnectionPool | None = None
_init_lock = threading.Lock()


def init_db_pool():
    global db_pool
    with _init_lock:
        if db_pool is not None:
            return
        try:
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = int(os.getenv("DB_PORT", "5432"))
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USERNAME")
            db_pass = os.getenv("DB_PASSWORD")

            logger.info(f"🚀 Connecting to DB: {db_host}:{db_port}")
            db_pool = ConnectionPool(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_pass,
                min_size=1,
                max_size=3,
                timeout=5.0,
            )
            logger.info("✅ Async DB Pool initialized.")
        except Exception as e:
            logger.error(f"❌ DB Init Error: {e}")
            raise


# -------------------------------------------------
# 3. 키워드 추출
# -------------------------------------------------
def extract_intent_keywords(query: str) -> List[str]:
    intent_map = {
        "job": {
            "triggers": [
                "취업",
                "일자리",
                "구직",
                "채용",
                "알바",
                "인턴",
                "근로",
                "고용",
            ],
            "keywords": ["%취업%", "%일자리%", "%구직%", "%고용%", "%채용%", "%근로%"],
        },
        "startup": {
            "triggers": ["창업", "스타트업", "사업", "1인기업", "예비창업"],
            "keywords": ["%창업%", "%스타트업%", "%사업화%"],
        },
        "housing": {
            "triggers": ["주거", "전세", "월세", "집", "임대", "보증금", "행복주택"],
            "keywords": ["%주거%", "%전세%", "%임대%", "%주택%", "%보증금%"],
        },
        "finance": {
            "triggers": ["금융", "대출", "이자", "적금", "자산", "목돈", "지원금"],
            "keywords": ["%금융%", "%대출%", "%융자%", "%적금%", "%이자%", "%지원금%"],
        },
    }
    found_keywords = []
    for category, data in intent_map.items():
        if any(trigger in query for trigger in data["triggers"]):
            found_keywords.extend(data["keywords"])

    if not found_keywords:
        words = query.split()
        found_keywords = [f"%{w}%" for w in words if len(w) >= 2]

    return list(set(found_keywords))


# -------------------------------------------------
# 4. MCP Tool: check_eligibility
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자가 상태를 입력하고 사용자의 상태에서 가장 관련성 높은 복지 제도를 추천합니다. 입력된 질문에서 나이, 성별, 지역 정보를 추출하여 가장 관련성 높은 서비스를 반환합니다.",
)
async def check_eligibility(
    query_text: str,
    age: int,
    gender: Literal["MALE", "FEMALE", "ALL"] = "ALL",
    sido: str | None = None,
    sigungu: str | None = None,
) -> Dict[str, Any]:

    if db_pool is None:
        await init_db_pool()

    # 1. 쿼리 전처리
    cleaned_query = re.sub(r"\d+살|\d+세|\d+", "", query_text).strip()
    if not cleaned_query:
        cleaned_query = query_text

    query_embedding = str(model.encode(cleaned_query).tolist())
    target_keywords = extract_intent_keywords(query_text)

    # 2. 지역명 전처리
    sido_pattern = f"%{sido[:2]}%" if sido and len(sido) >= 2 else "%"

    try:
        # [핵심 수정] 서브쿼리(Subquery) 구조로 변경
        # 안쪽(FROM 절 내부)에서 계산된 별명(Alias)들을 바깥쪽(Main Query)에서 안전하게 사용합니다.
        query = """
            SELECT * FROM (
                SELECT 
                    service_id, 
                    service_name, 
                    service_purpose, 
                    apply_url,
                    
                    -- [점수 1] 벡터 유사도 계산
                    (1 - (embedding <=> $1))::float AS vector_score,
                    
                    -- [점수 2] 의도(Intent) 매칭 보너스
                    (CASE 
                        WHEN service_name LIKE ANY($5::text[]) OR service_purpose LIKE ANY($5::text[]) THEN 0.5 
                        ELSE 0 
                    END)::float AS intent_bonus,

                    -- [점수 3] 프로필(Profile) 매칭 보너스
                    (
                        (CASE 
                            WHEN sido IS NULL OR sido = '' THEN 0.1   -- 전국 대상
                            WHEN sido ILIKE $3 THEN 0.2               -- 내 지역 일치
                            ELSE 0 
                        END) +
                        (CASE 
                            WHEN gender IS NULL OR gender = 'ALL' THEN 0.05 -- 성별 무관
                            WHEN gender = $4 THEN 0.1                       -- 성별 일치
                            ELSE 0 
                        END)
                    )::float AS profile_bonus
                    
                FROM welfare_service
                WHERE 
                    -- 최소한의 자격 요건 (나이)
                    (COALESCE(min_age, 0) <= $2 AND (max_age IS NULL OR max_age = 0 OR max_age >= $2))
            ) AS sub_query
            
            -- [정렬] 이제 'vector_score'가 진짜 컬럼처럼 인식됩니다.
            ORDER BY (vector_score + intent_bonus + profile_bonus) DESC
            LIMIT 5;
        """

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                query, query_embedding, age, sido_pattern, gender, target_keywords
            )

        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "url": r["apply_url"] if r["apply_url"] else "",
                # 디버깅: 점수 구성 확인
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
            "search_strategy": "Broad Intent Search (Error Fixed)",
            "recommended_services": services,
        }

    except Exception as e:
        logger.error(f"❌ Eligibility Error: {e}")
        return {"error": str(e)}
