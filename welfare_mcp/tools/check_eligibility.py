import logging
import os
from typing import Literal

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from backend.entity.UserProfile import UserProfile
from backend.entity.EligibilityResult import EligibilityResult
from mcp_container import mcp

from tools.OpenAPI.getOpenAPI import (
    get_welfare_supportConditions, 
    get_welfare_serviceDetail, 
    search_welfare_services
)

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# 1. DB 커넥션 풀 설정 (서버 시작 시 한 번만 생성)
# ----------------------------------------------------------------
# 최소 1개, 최대 10개의 연결을 유지합니다. 
# dbConn() 내부 설정을 가져오거나 환경 변수를 사용하세요.
try:
    # 기존 dbConn에서 정보를 가져오거나 직접 입력 (예시)
    db_pool = ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USERNAME"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT")
    )
    logger.info("Database Connection Pool created successfully.")
except Exception as e:
    logger.error(f"Failed to create Database Pool: {e}")
    db_pool = None

@mcp.tool(
    name="check_eligibility",
    description="사용자의 정확한 나이(숫자)와 가구 형태 등을 기반으로 신청 가능한 복지 서비스를 검색합니다."
)
async def check_eligibility(
    age: int,
    household_type: Literal[
        "SINGLE",
        "PARENT_CHILD",
        "COUPLE",
        "SINGLE_PARENT",
        "OTHER"
    ] = None,
    income_level: Literal["BELOW_MEDIAN_50", "MEDIAN_50_100", "MEDIAN_100_150", "ABOVE_MEDIAN_150", "UNKNOWN"] = "UNKNOWN",
    employment_status: Literal["EMPLOYED", "UNEMPLOYED", "STUDENT", "SELF_EMPLOYED", "UNKNOWN"] = "UNKNOWN"
) -> dict:
    """
    사용자의 나이와 가구 형태를 DB 조건과 비교하여
    신청 가능한 서비스 목록(Service ID 포함)을 반환합니다.
    """
    
    # 1. 가구 형태 매핑 (Enum -> DB 검색용 한글 키워드)
    household_map = {
        "SINGLE": "1인",
        "SINGLE_PARENT": "한부모",
        "COUPLE": "부부",
        "PARENT_CHILD": "다자녀",
        "OTHER": ""
    }
    keyword = household_map.get(household_type, "")
    eligible_services = []

    conn = None
    if not db_pool:
        return {"error": "Database connection pool is not initialized."}

    try:
        # 2. 풀에서 커넥션 획득 (새로 연결하지 않아 빠름)
        conn = db_pool.getconn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 3. SQL 쿼리 (인덱스 효율을 위해 LIKE 대신 = 혹은 최적화 고려)
        # 나이 조건과 가구 형태 조건을 필터링합니다.
        # 수정된 SQL 쿼리: 청년층에게 더 적합한 결과가 먼저 나오도록 정렬 추가
        query = """
            SELECT 
                service_id, service_name, service_purpose, support_target
            FROM welfare_service
            WHERE 
                (min_age <= %s AND max_age >= %s) -- 나이 구간에 정확히 일치하는 것을 우선
                AND (household_type IS NULL OR household_type LIKE %s)
            ORDER BY 
                CASE 
                    WHEN service_name LIKE '%%청년%%' THEN 1  -- '청년' 들어간 서비스 1순위
                    WHEN service_name LIKE '%%취업%%' THEN 2  -- '취업' 들어간 서비스 2순위
                    ELSE 3 
                END,
                service_id DESC
            LIMIT 5;
        """
        
        db_household_pattern = f"%{keyword}%" if keyword else "%"
        
        cur.execute(query, (age, age, db_household_pattern))
        rows = cur.fetchall()

        for row in rows:
            eligible_services.append({
                "service_id": row['service_id'],
                "name": row['service_name'],
                "purpose": row['service_purpose'],
                "target_text": row['support_target'],
                "url": row['apply_url']
            })

        cur.close()

    except Exception as e:
        logger.error(f"DB Query Error: {e}")
        return {"error": "데이터 조회 중 오류가 발생했습니다."}
    
    finally:
        # 4. 커넥션 반납 (종료하지 않고 풀로 되돌림)
        if conn:
            db_pool.putconn(conn)

    return {
        "count": len(eligible_services),
        "recommended_services": eligible_services
    }