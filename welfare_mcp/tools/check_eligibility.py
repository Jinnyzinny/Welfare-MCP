from backend.entity.UserProfile import UserProfile
from mcp_container import mcp
from typing import Literal

from backend.entity.EligibilityResult import EligibilityResult
from tools.OpenAPI.getOpenAPI import get_welfare_supportConditions, get_welfare_serviceDetail, search_welfare_services

from psycopg2 import DatabaseError

from backend.DB_Connection import dbConn

import logging
logging.basicConfig(level=logging.INFO)

@mcp.tool(
    name="check_eligibility",
    description="사용자 프로필과 서비스의 지원대상/선정기준을 비교하여 신청 가능성을 판단합니다."
)
async def check_eligibility(
        # 연령대를 3가지로 구분
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = None,

    # 소득 수준을 5단계로 구분
    income_level: Literal[
            "BELOW_MEDIAN_50",
            "MEDIAN_50_100",
            "MEDIAN_100_150",
            "ABOVE_MEDIAN_150",
            "UNKNOWN"
    ] = None,
    # 고용 상태를 5가지로 분류
    employment_status: Literal[
            "EMPLOYED",
            "UNEMPLOYED",
            "STUDENT",
            "SELF_EMPLOYED",
            "UNKNOWN"
    ] = None,
    # 가구 형태를 5가지로 구분
    household_type: Literal[
            "SINGLE",
            "PARENT_CHILD",
            "COUPLE",
            "SINGLE_PARENT",
            "OTHER"
    ] = None
) -> dict:
    """
    서비스의 자연어 조건을 UserProfile과 비교하여
    신청 가능 여부 및 사유를 반환합니다.
    """
    profile = UserProfile(
        age_group=age_group,
        income_level=income_level,
        employment_status=employment_status,
        household_type=household_type
    )

    reasons = []
    missing = []
    
    # DB 연결
    try:
        conn = dbConn()
        # DB 커서 생성
        cur = conn.cursor()
    except DatabaseError as e:
        logging.error(f"DB 연결 실패: {e}")
        raise

    cur.execute("select * from welfare_service")
    service=cur.fetchall()



    # 안 써도 되는 걸 알지만 StereoType처럼 추가 단 SELECT밖에 하지 않았기에 주석 처리
    # conn.commit()
    # DB 연결 종료
    cur.close()
    
    return EligibilityResult(
        is_eligible=False,
        reasons=reasons,
        missing_information=missing,
        user_profile=profile
    ).model_dump()