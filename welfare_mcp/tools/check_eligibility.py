import logging
import os
import asyncio
from typing import Literal, List, Dict, Any

import asyncpg
from mcp_container import mcp
# [수정] 필요한 라이브러리 추가
from sentence_transformers import SentenceTransformer

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# [수정] 모델을 전역에서 한 번만 로드 (함수 밖으로 빼야 함)
logger.info("📡 Loading Embedding Model...")
model = SentenceTransformer('jhgan/ko-sroberta-multitask')
logger.info("✅ Model loaded successfully.")

# -------------------------------------------------
# DB Pool 전역 관리 및 초기화 락
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
# MCP Tool: check_eligibility
# -------------------------------------------------
@mcp.tool(
    name="check_eligibility",
    description="사용자의 질문 내용과 나이, 가구 형태를 기반으로 적합한 복지 서비스를 검색합니다."
)
async def check_eligibility(
    query_text: str, 
    age: int,
    household_type: Literal["SINGLE", "PARENT_CHILD", "COUPLE", "SINGLE_PARENT", "OTHER"] | None = None
) -> Dict[str, Any]:
    
    if db_pool is None:
        await init_db_pool()

    # 가구 형태 매핑
    household_map = {
        "SINGLE": "1인",
        "SINGLE_PARENT": "한부모",
        "COUPLE": "부부",
        "PARENT_CHILD": "다자녀"
    }
    keyword = household_map.get(household_type, "")
    pattern = f"%{keyword}%" if keyword else "%"

    try:
        # [핵심] 텍스트 임베딩 변환
        # asyncpg는 리스트 형태의 벡터를 지원하므로 .tolist() 필수
        query_embedding = model.encode(query_text).tolist()

        # 쿼리 실행
        query = """
            SELECT 
                service_id, 
                service_name, 
                service_purpose, 
                support_target, 
                apply_url,
                1 - (embedding <=> $1) AS similarity
            FROM welfare_service
            WHERE (min_age <= $2 AND max_age >= $2)
              AND (household_type IS NULL OR household_type ILIKE $3)
            ORDER BY embedding <=> $1
            LIMIT 5;
        """

        async with db_pool.acquire() as conn:
            # $1: embedding, $2: age, $3: pattern
            rows = await conn.fetch(query, query_embedding, age, pattern)
            
        services = [
            {
                "service_id": r["service_id"],
                "name": r["service_name"],
                "purpose": r["service_purpose"],
                "url": r["apply_url"] if r["apply_url"] else "",
                "similarity": round(float(r["similarity"]), 4) # 유사도 확인용
            } for r in rows
        ]
        
        return {
            "count": len(services),
            "recommended_services": services
        }
    except Exception as e:
        # [수정] 에러 로그를 아주 상세하게 출력하도록 변경 (원인 파악용)
        logger.error(f"❌ DB Query Error: {str(e)}", exc_info=True)
        return {"error": f"조회 중 오류가 발생했습니다: {str(e)}"}