from backend.entity.UserProfile import UserProfile
from mcp_container import mcp
from typing import Literal

from backend.entity.EligibilityResult import EligibilityResult
from tools.OpenAPI.getOpenAPI import get_welfare_supportConditions, get_welfare_serviceDetail, search_welfare_services

from psycopg2 import DatabaseError

from backend.DB_Connection import dbConn

from psycopg2.extras import RealDictCursor

import logging
logging.basicConfig(level=logging.INFO)
@mcp.tool(
    name="check_eligibility",
    description="사용자의 정확한 나이(숫자)와 가구 형태 등을 기반으로 신청 가능한 복지 서비스를 검색합니다."
)
async def check_eligibility(
    # [핵심] DB 비교를 위해 정확한 숫자 나이를 입력받음
    age: int,

    # 가구 형태 (Enum -> 내부에서 한글 키워드로 변환)
    household_type: Literal[
            "SINGLE",
            "PARENT_CHILD",
            "COUPLE",
            "SINGLE_PARENT",
            "OTHER"
    ] = None,

    # 참고용 (DB 검색 조건에는 안 쓰이지만 프로필 생성용)
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
    try:
        conn = dbConn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 2. SQL 쿼리 (나이 범위 & 가구 형태 검색)
        query = """
            SELECT 
                service_id, 
                service_name, 
                service_purpose, 
                support_target,
                apply_url,
                min_age,
                max_age
            FROM welfare_service
            WHERE 
                (min_age IS NULL OR min_age <= %s) 
                AND (max_age IS NULL OR max_age >= %s)
                AND (household_type IS NULL OR household_type LIKE %s)
            LIMIT 5;
        """
        
        db_household_pattern = f"%{keyword}%" if keyword else "%"
        
        cur.execute(query, (age, age, db_household_pattern))
        rows = cur.fetchall()

        for row in rows:
            eligible_services.append({
                "service_id": row['service_id'], # [중요] 이 ID가 다음 툴의 인풋이 됩니다.
                "name": row['service_name'],
                "purpose": row['service_purpose'],
                "target_text": row['support_target'],
                "url": row['apply_url']
            })

    except Exception as e:
        logging.error(f"DB Query Error: {e}")
        return {"error": str(e)}
    finally:
        if conn:
            conn.close()

    return {
        "count": len(eligible_services),
        "recommended_services": eligible_services
    }