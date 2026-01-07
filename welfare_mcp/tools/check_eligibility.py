from user_profile import UserProfile
from mcp_container import mcp

from backend.entity.EligibilityResult import EligibilityResult

@mcp.tool(
    name="check_eligibility",
    description="사용자 프로필과 서비스의 지원대상/선정기준을 비교하여 신청 가능성을 판단합니다."
)
def check_eligibility(
    user_profile: dict,
    service: dict
) -> dict:
    """
    서비스의 자연어 조건을 UserProfile과 비교하여
    신청 가능 여부 및 사유를 반환합니다.
    """
    profile = UserProfile(**user_profile)

    reasons = []
    missing = []

    support_target = service.get("support_target", "")
    selection_criteria = service.get("selection_criteria", "")

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

    return {
        "service_id": service.get("service_id"),
        "eligible": eligible,
        "reasons": reasons if reasons else ["신청 가능성이 있습니다."],
        "missing_conditions": missing
    }
