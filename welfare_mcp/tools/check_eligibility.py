from backend.entity.UserProfile import UserProfile
from mcp_container import mcp
from typing import Literal

from backend.entity.EligibilityResult import EligibilityResult
from resources.getOpenAPI import get_welfare_supportConditions, get_welfare_serviceDetail, search_welfare_services

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

    service = await get_welfare_serviceDetail()
    item = service.get("data", [])

    if not item:
        raise ValueError("서비스 정보 조회 실패")

    service_detail = item[0]
    service_id = service_detail.get("서비스ID")

    if not service_id:
        raise ValueError("service_id를 찾을 수 없음")

    support_target = service_detail.get("지원대상")
    selection_criteria = service_detail.get("선정기준")

    # 1️⃣ 연령 판별 (문자열 기반)
    if "청년" in support_target:
        if profile.age_group is None:
            missing.append("age_group")
        elif profile.age_group != "YOUTH":
            reasons.append("청년 대상 서비스입니다.")

    # 2️⃣ 미취업 여부
    if "미취업" in support_target:
        if profile.employment_status is None:
            missing.append("employment_status")
        elif profile.employment_status != "UNEMPLOYED":
            reasons.append("미취업자 대상 서비스입니다.")

    # 3️⃣ 소득 기준
    if "중위소득" in selection_criteria:
        if profile.income_level is None:
            missing.append("income_level")
        elif profile.income_level in ["ABOVE_MEDIAN_150"]:
            reasons.append("소득 기준을 초과할 가능성이 있습니다.")

    eligible = len(reasons) == 0 and len(missing) == 0

    able_to_get_welfare=EligibilityResult(
        service_id=service_id,
        eligible=eligible,
        reasons=reasons if reasons else ["신청 가능성이 있습니다."],
        missing_conditions=missing
    )
    
    return able_to_get_welfare.model_dump()