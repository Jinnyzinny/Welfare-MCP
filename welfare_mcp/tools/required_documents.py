import logging
import os
from typing import List, Literal

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from backend.entity.UserProfile import UserProfile
from mcp_container import mcp

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# 1. DB 커넥션 풀 설정 (전역 변수)
# ----------------------------------------------------------------
try:
    db_pool = ThreadedConnectionPool(
        minconn=1,
        maxconn=10,
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USERNAME"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT")
    )
    logger.info("Database ThreadedConnectionPool initialized.")
except Exception as e:
    logger.error(f"Failed to initialize DB Pool: {e}")
    db_pool = None

@mcp.tool(
    name="required_documents",
    description="선택한 서비스의 ID와 사용자 프로필을 기반으로 DB에서 구비서류 목록을 조회합니다."
)
async def required_documents(
    service_id: str,  # check_eligibility 결과로 얻은 서비스 ID
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = "ADULT",
    income_level: Literal["BELOW_MEDIAN_50", "MEDIAN_50_100", "MEDIAN_100_150", "ABOVE_MEDIAN_150", "UNKNOWN"] = "UNKNOWN",
    employment_status: Literal["EMPLOYED", "UNEMPLOYED", "STUDENT", "SELF_EMPLOYED", "UNKNOWN"] = "UNKNOWN",
    household_type: Literal["SINGLE", "PARENT_CHILD", "COUPLE", "SINGLE_PARENT", "OTHER"] = "OTHER"
) -> dict:
    """
    DB에서 서비스 정보를 조회하여 공통 서류와 사용자 맞춤형 서류를 정리합니다.
    """
    
    # 1. 사용자 프로필 객체 생성
    profile = UserProfile(
        age_group=age_group,
        income_level=income_level,
        employment_status=employment_status,
        household_type=household_type
    )

    conn = None
    if not db_pool:
        return {"error": "Database connection pool is not available."}

    try:
        # 2. 풀에서 커넥션 가져오기
        conn = db_pool.getconn()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 3. SQL 쿼리 실행
        # 테이블/컬럼명은 실제 DB 구조(welfare_service)에 맞춰 'required_documents' 등으로 조회
        query = """
            SELECT 
                service_id, 
                required_documents, 
                official_required_documents,
                personal_verification_documents,
                self_documents
            FROM welfare_service
            WHERE service_id = %s;
        """
        cur.execute(query, (service_id,))
        row = cur.fetchone()

        if not row:
            return {"error": f"서비스 ID '{service_id}'를 DB에서 찾을 수 없습니다."}

        # 4. 서류 파싱 함수
        def parse_docs(doc_str: str) -> List[str]:
            if not doc_str:
                return []
            # 쉼표나 줄바꿈으로 구분된 서류명을 리스트로 분리
            return [d.strip() for d in doc_str.replace("\n", ",").split(",") if d.strip()]

        required_now = parse_docs(row.get('required_documents', ""))
        required_now.extend(parse_docs(row.get('self_documents', "")))
        verified_by_officer = parse_docs(row.get('officer_documents', ""))
        
        conditional = []

        # 5. 사용자 프로필 기반 조건부 서류 추가
        if profile.employment_status == "UNEMPLOYED":
            conditional.append("고용보험 미가입 확인서")
        elif profile.employment_status == "EMPLOYED":
            conditional.append("근로소득 원천징수영수증")
        elif profile.employment_status == "STUDENT":
            conditional.append("재학증명서")

        if profile.income_level in ["BELOW_MEDIAN_50", "MEDIAN_50_100"]:
            conditional.append("소득금액증명원")

        # 6. 결과 반환 및 중복 제거
        return {
            "service_id": service_id,
            "required_now": list(dict.fromkeys(required_now)),
            "conditional": list(dict.fromkeys(conditional)),
            "verified_by_officer": list(dict.fromkeys(verified_by_officer)),
            "notes": [
                "정확한 서류는 접수기관에서 최종 확인이 필요합니다.",
                "공무원 확인 서류는 행정정보 공동이용을 통해 확인되므로 별도 제출이 필요하지 않을 수 있습니다."
            ]
        }

    except Exception as e:
        logger.error(f"Error in required_documents: {e}")
        return {"error": "데이터베이스 조회 중 오류가 발생했습니다."}
    
    finally:
        # 7. 커넥션을 닫지 않고 풀에 반납
        if conn:
            db_pool.putconn(conn)