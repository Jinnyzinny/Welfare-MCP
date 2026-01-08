from typing import List
from backend.entity.UserProfile import UserProfile
from mcp_container import mcp
from typing import Literal
from welfare_mcp.resources.getOpenAPI import get_welfare_serviceDetail, get_welfare_supportConditions, search_welfare_services

@mcp.tool(
    name="required_documents",
    description="사용자 프로필과 서비스 정보를 기반으로 구비서류를 정리합니다."
)
async def required_documents(
    # 연령대를 3가지로 구분
    age_group: Literal["YOUTH", "ADULT", "SENIOR"] = None,

    # 소득 수준을 5단계로 구분
    income_level:
        Literal[
            "BELOW_MEDIAN_50",
            "MEDIAN_50_100",
            "MEDIAN_100_150",
            "ABOVE_MEDIAN_150",
            "UNKNOWN"
        
    ] = None,
    # 고용 상태를 5가지로 분류
    employment_status: 
        Literal[
            "EMPLOYED",
            "UNEMPLOYED",
            "STUDENT",
            "SELF_EMPLOYED",
            "UNKNOWN"
        
    ] = None,
    # 가구 형태를 5가지로 구분
    household_type: 
        Literal[
            "SINGLE",
            "PARENT_CHILD",
            "COUPLE",
            "SINGLE_PARENT",
            "OTHER"
        
    ] = None
) -> dict:
    """
    서비스의 구비서류 정보를 사용자 상태에 맞게 정리합니다.
    확정이 아닌 '준비 가능 목록'을 제공합니다.
    """
    profile = UserProfile(
        age_group=age_group,
        income_level=income_level,
        employment_status=employment_status,
        household_type=household_type
    )
    service = await get_welfare_serviceDetail()
    item = service.get("data", [])

    if not item:
        raise ValueError("서비스 정보 조회 실패")

    service_detail = item[0]
    service_id = service_detail.get("서비스ID")

    if not service_id:
        raise ValueError("service_id를 찾을 수 없음")

    # 원문 필드
    raw_docs = service.get("구비서류", "")
    officer_docs = service.get("공무원확인구비서류", "")
    self_docs = service.get("본인확인필요구비서류", "")

    required_now: List[str] = []
    conditional: List[str] = []
    verified_by_officer: List[str] = []
    notes: List[str] = []

    # 1 공통 서류 (항상)
    if raw_docs:
        required_now.extend(
            [d.strip() for d in raw_docs.split(",") if d.strip()]
        )

    # 2 본인 확인 서류
    if self_docs:
        required_now.extend(
            [d.strip() for d in self_docs.split(",") if d.strip()]
        )

    # 3 공무원 확인 서류 (사용자 준비 불필요)
    if officer_docs:
        verified_by_officer.extend(
            [d.strip() for d in officer_docs.split(",") if d.strip()]
        )

    # 4 사용자 상태 기반 조건부 서류 판단

    # 미취업자
    if profile.employment_status == "UNEMPLOYED":
        conditional.append("고용보험 미가입 확인서")
    elif profile.employment_status == "EMPLOYED":
        conditional.append("근로소득 원천징수영수증")

    # 학생
    if profile.employment_status == "STUDENT":
        conditional.append("재학증명서")

    # 소득 기준
    if profile.income_level in ["BELOW_MEDIAN_50", "MEDIAN_50_100"]:
        conditional.append("소득금액증명원")

    # 주택 보유
    if profile.assets and profile.assets.get("has_real_estate"):
        conditional.append("부동산 등기부등본")

    # 5 안내 문구
    notes.append("정확한 서류는 접수기관에서 최종 확인합니다.")
    notes.append("공무원 확인 서류는 별도 제출이 필요하지 않을 수 있습니다.")



    return {
        "service_id": service_id,
        "required_now": list(set(required_now)),
        "conditional": list(set(conditional)),
        "verified_by_officer": list(set(verified_by_officer)),
        "notes": notes
    }
