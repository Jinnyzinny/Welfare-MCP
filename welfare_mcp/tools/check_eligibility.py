import logging
import os
import re
import asyncio
from typing import Literal, List, Dict, Any

import asyncpg
from mcp_container import mcp
from sentence_transformers import SentenceTransformer

# -------------------------------------------------
# 1. 설정 및 모델 로드 (동일)
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("📡 Loading Embedding Model (jhgan/ko-sroberta-multitask)...")
model = SentenceTransformer('jhgan/ko-sroberta-multitask')
logger.info("✅ Model loaded successfully.")

# -------------------------------------------------
# 2. DB Pool (동일)
# -------------------------------------------------
db_pool: asyncpg.Pool | None = None
_init_lock = asyncio.Lock()

async def init_db_pool():
    global db_pool
    async with _init_lock:
        if db_pool is not None: return
        try:
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = int(os.getenv("DB_PORT", "5432"))
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USERNAME")
            db_pass = os.getenv("DB_PASSWORD")

            logger.info(f"🚀 Connecting to DB: {db_host}:{db_port}")
            db_pool = await asyncpg.create_pool(
                host=db_host, port=db_port, database=db_name,
                user=db_user, password=db_pass,
                min_size=1, max_size=3, timeout=5.0
            )
            logger.info("✅ Async DB Pool initialized.")
        except Exception as e:
            logger.error(f"❌ DB Init Error: {e}")
            raise 

# -------------------------------------------------
# 3. 키워드 추출 (동일)
# -------------------------------------------------
def extract_intent_keywords(query: str) -> List[str]:
    # (이전과 동일한 로직 유지)
    intent_map = {
        "job": {
            "triggers": ["취업", "일자리", "구직", "채용", "알바", "인턴", "근로", "고용"],
            "keywords": ["%취업%", "%일자리%", "%구직%", "%고용%", "%채용%", "%근로%"]
        },
        "startup": {
            "triggers": ["창업", "스타트업", "사업", "1인기업", "예비창업"],
            "keywords": ["%창업%", "%스타트업%", "%사업화%"]
        },
        "housing": {
            "triggers": ["주거", "전세", "월세", "집", "임대", "보증금", "행복주택"],
            "keywords": ["%주거%", "%전세%", "%임대%", "%주택%", "%보증금%"]
        },
        "finance": {
            "triggers": ["금융", "대출", "이자", "적금", "자산", "목돈", "지원금"],
            "keywords": ["%금융%", "%대출%", "%융자%", "%적금%", "%이자%", "%지원금%"]
        }
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
# 4. MCP Tool: check_eligibility (유연한 검색)
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 지역, 성별, 나이를 기반으로 최적의 서비스를 검색합니다. (유연한 매칭)"
)
async def check_eligibility(
    query_text: str, 
    age: int,
    gender: Literal["MALE", "FEMALE", "ALL"] = "ALL",
    sido: str | None = None,
    sigungu: str | None = None
) -> Dict[str, Any]:
    
    if db_pool is None: await init_db_pool()

    # 1. 쿼리 전처리
    cleaned_query = re.sub(r'\d+살|\d+세|\d+', '', query_text).strip()
    if not cleaned_query: cleaned_query = query_text
    
    query_embedding = str(model.encode(cleaned_query).tolist())
    target_keywords = extract_intent_keywords(query_text)

    # 2. 지역명 전처리 (유연한 매칭을 위해)
    # 예: "서울특별시" -> "%서울%"
    sido_pattern = f"%{sido[:2]}%" if sido and len(sido) >= 2 else "%"

    try:
        # [핵심 변경] WHERE 절을 최소화하고 ORDER BY에 점수 로직 집중
        query = """
            SELECT 
                service_id, 
                service_name, 
                service_purpose, 
                apply_url,
                -- 디버깅용 점수 출력
                (1 - (embedding <=> $1))::float AS vector_score,
                
                -- [점수 계산 로직]
                (
                    -- 1. 키워드 매칭 (+0.4점)
                    (CASE 
                        WHEN service_name LIKE ANY($5::text[]) OR service_purpose LIKE ANY($5::text[]) THEN 0.4 
                        ELSE 0 
                    END) +
                    -- 2. 지역 일치 (+0.3점) - 전국(NULL)이거나 내 지역과 비슷하면 점수 부여
                    (CASE 
                        WHEN sido IS NULL OR sido = '' THEN 0.1  -- 전국 지원은 기본 점수
                        WHEN sido ILIKE $3 THEN 0.3              -- 내 지역이면 가산점
                        ELSE 0 
                    END) +
                    -- 3. 성별 일치 (+0.1점)
                    (CASE 
                        WHEN gender IS NULL OR gender = 'ALL' THEN 0.05
                        WHEN gender = $4 THEN 0.1 
                        ELSE 0 
                    END)
                )::float AS total_bonus
                 
            FROM welfare_service
            WHERE 
                -- [최소한의 필터] 나이는 법적 요건이므로 지켜야 함 (단, NULL 안전 처리)
                (COALESCE(min_age, 0) <= $2 AND (max_age IS NULL OR max_age = 0 OR max_age >= $2))
                
                -- [지역 필터 완화]
                -- 아예 다른 지역(예: 서울인데 부산 서비스)은 제외하되, 
                -- '전국(NULL)'이나 '빈 값'은 살려둠.
                AND (sido IS NULL OR sido = '' OR sido ILIKE $3)

            -- [정렬] 벡터 점수 + 보너스 점수 합산 내림차순
            ORDER BY (vector_score + total_bonus) DESC
            LIMIT 5;
        """

        async with db_pool.acquire() as conn:
            # $3에 sido_pattern("%서울%") 전달하여 ILIKE 매칭 유도
            rows = await conn.fetch(query, query_embedding, age, sido_pattern, gender, target_keywords)
            
        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "url": r["apply_url"] if r["apply_url"] else "",
                # 점수 확인용
                "total_score": round(r["vector_score"] + r["total_bonus"], 4)
            } for r in rows
        ]
        
        return {
            "count": len(services),
            "search_strategy": "Soft Filtering (Score-based)",
            "query_used": cleaned_query,
            "recommended_services": services
        }
        
    except Exception as e:
        logger.error(f"❌ Eligibility Error: {e}")
        return {"error": str(e)}