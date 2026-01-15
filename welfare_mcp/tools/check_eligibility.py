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
# 모델을 전역에서 한 번만 로드 (메모리 효율)
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
        if db_pool is not None:
            return
        try:
            db_host = os.getenv("DB_HOST", "postgres")
            db_port = int(os.getenv("DB_PORT", "5432"))
            db_name = os.getenv("DB_NAME")
            db_user = os.getenv("DB_USERNAME")
            db_pass = os.getenv("DB_PASSWORD")

            logger.info(f"🚀 Connecting to DB: {db_host}:{db_port}")

            db_pool = await asyncpg.create_pool(
                host=db_host,
                port=db_port,
                database=db_name,
                user=db_user,
                password=db_pass,
                min_size=1,
                max_size=3,
                timeout=5.0
            )
            logger.info("✅ Async DB Pool initialized successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to initialize DB pool: {e}")
            raise 

# -------------------------------------------------
# 3. 유틸리티 함수: 의도 기반 키워드 추출
# -------------------------------------------------
def extract_intent_keywords(query: str) -> List[str]:
    """
    사용자 질문에서 핵심 의도를 파악하여 SQL 검색용 키워드(LIKE 패턴) 리스트를 반환합니다.
    하드코딩을 피하고 범용성을 갖추기 위한 로직입니다.
    """
    # 1. 카테고리별 매핑 사전
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
    
    # 2. 질문에 트리거 단어가 있는지 확인
    for category, data in intent_map.items():
        if any(trigger in query for trigger in data["triggers"]):
            found_keywords.extend(data["keywords"])
    
    # 3. 매칭된 의도가 없으면, 질문에서 명사형 단어만 추출해서 사용 (Fallback)
    if not found_keywords:
        # 간단히 공백 기준 분리 후 2글자 이상인 단어만 앞뒤 % 붙임
        words = query.split()
        found_keywords = [f"%{w}%" for w in words if len(w) >= 2]
        
    # 중복 제거 후 반환
    return list(set(found_keywords))

# -------------------------------------------------
# 4. MCP Tool: check_eligibility
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 질문과 나이, 가구 형태를 기반으로 적합한 복지 서비스를 검색합니다."
)
async def check_eligibility(
    query_text: str, 
    age: int,
    household_type: Literal["SINGLE", "PARENT_CHILD", "COUPLE", "SINGLE_PARENT", "OTHER"] | None = None
) -> Dict[str, Any]:
    
    if db_pool is None:
        await init_db_pool()

    # --- [Step 1] 쿼리 정제 (숫자 노이즈 제거) ---
    # "28살" 같은 숫자가 임베딩 벡터 방향을 왜곡하지 않도록 제거합니다.
    cleaned_query = re.sub(r'\d+살|\d+세|\d+대', '', query_text).strip()
    
    # --- [Step 2] 임베딩 생성 ---
    # 숫자가 제거된 순수 텍스트("취업 지원")로 벡터를 만듭니다.
    query_embedding = str(model.encode(cleaned_query).tolist())

    # --- [Step 3] 동적 키워드 추출 ---
    # 질문의 의도에 맞는 키워드 리스트를 가져옵니다. (예: ['%취업%', '%구직%'])
    target_keywords = extract_intent_keywords(cleaned_query)

    # 가구 형태 매핑
    household_map = {
        "SINGLE": "1인",
        "SINGLE_PARENT": "한부모",
        "COUPLE": "부부",
        "PARENT_CHILD": "다자녀"
    }
    keyword = household_map.get(household_type, "")
    household_pattern = f"%{keyword}%" if keyword else "%"

    try:
        # --- [Step 4] 하이브리드 검색 SQL 실행 ---
        # 벡터 유사도(Semantic) + 키워드 매칭(Lexical) = 정확도 향상
        # $4 파라미터로 키워드 배열(text[])을 넘깁니다.
        
        sql = """
            SELECT 
                service_id, 
                service_name, 
                service_purpose, 
                apply_url,
                -- 1. 벡터 유사도 점수 (0~1)
                (1 - (embedding <=> $1)) AS vector_score,
                
                -- 2. 키워드 보너스 점수
                -- 제목이나 목적이 추출된 키워드 중 하나라도 포함하면(LIKE ANY) 가산점 부여
                (CASE 
                    WHEN service_name LIKE ANY($4::text[]) OR service_purpose LIKE ANY($4::text[]) THEN 0.5
                    ELSE 0 
                 END) AS keyword_bonus
            FROM welfare_service
            WHERE (min_age <= $2 AND max_age >= $2)
            AND (household_type IS NULL OR household_type ILIKE $3)
            
            -- [정렬 기준] 벡터 점수와 키워드 점수를 합산하여 내림차순 정렬
            ORDER BY (1 - (embedding <=> $1)) + 
                     (CASE 
                        WHEN service_name LIKE ANY($4::text[]) OR service_purpose LIKE ANY($4::text[]) THEN 0.5
                        ELSE 0 
                      END) DESC
            LIMIT 5;
        """

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(sql, query_embedding, age, household_pattern, target_keywords)
            
        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "apply_url": r["apply_url"] if r["apply_url"] else "",
                
                # [수정] r["keyword_bonus"]가 Decimal 타입이므로 float로 변환 후 더해야 합니다.
                "score": round(float(r["vector_score"]) + float(r["keyword_bonus"]), 4),
                
                "match_type": "Hybrid Match" if r["keyword_bonus"] > 0 else "Vector Only"
            } for r in rows
        ]
        
        return {
            "count": len(services),
            "search_intent": cleaned_query,     # 정제된 쿼리 확인용
            "active_keywords": target_keywords, # 적용된 키워드 확인용
            "recommended_services": services
        }
        
    except Exception as e:
        logger.error(f"❌ DB Query Error: {str(e)}", exc_info=True)
        return {"error": f"서비스 조회 중 오류가 발생했습니다: {str(e)}"}