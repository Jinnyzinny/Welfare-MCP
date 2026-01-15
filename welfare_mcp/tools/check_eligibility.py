import logging
import os
import re
import asyncio
from typing import Literal, List, Dict, Any

import asyncpg
from mcp_container import mcp
from sentence_transformers import SentenceTransformer

# -------------------------------------------------
# 1. 설정 및 모델 로드
# -------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logger.info("📡 Loading Embedding Model (jhgan/ko-sroberta-multitask)...")
model = SentenceTransformer('jhgan/ko-sroberta-multitask')
logger.info("✅ Model loaded successfully.")

# -------------------------------------------------
# 2. DB Pool 전역 관리
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
# 3. 유틸리티 함수: 의도 기반 키워드 추출
# -------------------------------------------------
def extract_intent_keywords(query: str) -> List[str]:
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
            "triggers": ["금융", "대출", "이자", "적금", "자산", "목돈"],
            "keywords": ["%금융%", "%대출%", "%융자%", "%적금%", "%이자%"]
        },
        "care": {
            "triggers": ["육아", "돌봄", "어린이집", "보육", "임신", "출산"],
            "keywords": ["%육아%", "%돌봄%", "%보육%", "%임신%", "%출산%"]
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
# 4. MCP Tool: check_eligibility
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 지역, 성별, 나이를 기반으로 최적의 서비스를 검색합니다."
)
async def check_eligibility(
    query_text: str, 
    age: int,
    gender: Literal["MALE", "FEMALE", "ALL"] = "ALL",
    sido: str | None = None,
    sigungu: str | None = None
) -> Dict[str, Any]:
    
    if db_pool is None: await init_db_pool()

    # 1. 전처리 및 임베딩
    cleaned_query = re.sub(r'\d+살|\d+세', '', query_text).strip()
    query_embedding = str(model.encode(cleaned_query).tolist())
    
    # [중요] 2. 키워드 추출 실행 (이전 코드에서 누락된 부분)
    target_keywords = extract_intent_keywords(cleaned_query)

    try:
        # [수정] LIKE ANY($5)를 사용하여 동적 키워드 매칭
        query = """
            SELECT 
                service_id, service_name, service_purpose, apply_url,
                (1 - (embedding <=> $1)) AS vector_score,
                (CASE 
                    WHEN service_name LIKE ANY($5::text[]) OR service_purpose LIKE ANY($5::text[]) THEN 1
                    ELSE 0 
                 END) AS is_keyword_match
            FROM welfare_service
            WHERE (min_age <= $2 AND max_age >= $2)
            AND (sido IS NULL OR sido = $3)
            AND (gender = 'ALL' OR gender = $4)
            ORDER BY is_keyword_match DESC, vector_score DESC
            LIMIT 5;
        """

        async with db_pool.acquire() as conn:
            # $5 자리에 target_keywords 리스트 전달
            rows = await conn.fetch(query, query_embedding, age, sido, gender, target_keywords)
            
        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "url": r["apply_url"] if r["apply_url"] else ""
            } for r in rows
        ]
        
        return {"count": len(services), "recommended_services": services}
    except Exception as e:
        logger.error(f"❌ Eligibility Error: {e}")
        return {"error": str(e)}