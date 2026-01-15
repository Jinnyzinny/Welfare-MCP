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
# 2. DB Pool
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
# 3. 키워드 추출 (핵심 의도 파악용)
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
# 4. MCP Tool: check_eligibility (Broad Search & Ranking)
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 의도를 최우선으로 검색하고, 프로필(지역/성별)은 정렬 가산점으로 활용합니다."
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

    # 2. 지역명 전처리 (유연한 매칭용)
    sido_pattern = f"%{sido[:2]}%" if sido and len(sido) >= 2 else "%"

    try:
        # [핵심 전략 변경] WHERE 절은 최소화, ORDER BY에 로직 집중
        query = """
            SELECT 
                service_id, 
                service_name, 
                service_purpose, 
                apply_url,
                
                -- [점수 1] 벡터 유사도 (기본 점수)
                (1 - (embedding <=> $1))::float AS vector_score,
                
                -- [점수 2] 의도(Intent) 매칭 보너스 (가장 중요, +0.5점)
                -- 사용자가 '취업'을 물어봤는데 서비스명에 '취업'이 있으면 강력 추천
                (CASE 
                    WHEN service_name LIKE ANY($5::text[]) OR service_purpose LIKE ANY($5::text[]) THEN 0.5 
                    ELSE 0 
                END)::float AS intent_bonus,

                -- [점수 3] 프로필(Profile) 매칭 보너스 (보조 점수, +0.1~0.2점)
                -- 지역이나 성별이 안 맞아도 검색은 되지만, 맞으면 상단으로 올림
                (
                    (CASE 
                        WHEN sido IS NULL OR sido = '' THEN 0.1    -- 전국 서비스는 기본 점수
                        WHEN sido ILIKE $3 THEN 0.2                -- 내 지역이면 가산점
                        ELSE 0                                     -- 다른 지역이면 0점 (제외는 안 함)
                    END) +
                    (CASE 
                        WHEN gender IS NULL OR gender = 'ALL' THEN 0.05
                        WHEN gender = $4 THEN 0.1 
                        ELSE 0 
                    END)
                )::float AS profile_bonus
                 
            FROM welfare_service
            WHERE 
                -- [최소한의 안전장치] 나이는 법적 제한이므로 유지하되, 데이터가 없으면(NULL) 허용
                (COALESCE(min_age, 0) <= $2 AND (max_age IS NULL OR max_age = 0 OR max_age >= $2))
                
                -- [지역/성별 하드 필터 삭제]
                -- 28세 남성이 '여성 전용'이나 '부산' 공고를 볼 수도 있게 함 (단, 순위는 낮아짐)

            -- [최종 정렬] 의도 > 벡터 > 프로필 순으로 영향력을 미침
            ORDER BY (vector_score + intent_bonus + profile_bonus) DESC
            LIMIT 5;
        """

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(query, query_embedding, age, sido_pattern, gender, target_keywords)
            
        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "url": r["apply_url"] if r["apply_url"] else "",
                # 점수 디버깅용: 어느 요소 때문에 추천되었는지 확인 가능
                "score_breakdown": {
                    "vector": round(r["vector_score"], 2),
                    "intent": round(r["intent_bonus"], 2),
                    "profile": round(r["profile_bonus"], 2),
                    "total": round(r["vector_score"] + r["intent_bonus"] + r["profile_bonus"], 2)
                }
            } for r in rows
        ]
        
        return {
            "count": len(services),
            "strategy": "Broad Intent Search",
            "recommended_services": services
        }
        
    except Exception as e:
        logger.error(f"❌ Eligibility Error: {e}")
        return {"error": str(e)}