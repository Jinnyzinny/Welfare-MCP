from typing import Literal, List
from fastapi_mcp import FastApiMCP

from ...fastapi.entity.user_profile import UserProfile

mcp = FastApiMCP(
    name="Welfare Services MCP",
    description="MCP for accessing welfare services information"
)

@mcp.tool(
    name="collect_basic_profile",
    description="사용자의 기본 프로필(연령대, 소득 수준, 경제활동 상태)을 수집합니다."
)
def collect_basic_profile(
    age_group: Literal["YOUTH", "ADULT", "SENIOR"],
    income_level: Literal[
        "BELOW_MEDIAN_50",
        "MEDIAN_50_100",
        "MEDIAN_100_150",
        "ABOVE_MEDIAN_150",
        "UNKNOWN"
    ],
    employment_status: Literal[
        "EMPLOYED",
        "UNEMPLOYED",
        "STUDENT",
        "SELF_EMPLOYED",
        "UNKNOWN"
    ]
) -> dict:
    """
    사용자 기본 프로필을 생성합니다.
    연령대, 가구 소득 수준, 경제활동 상태를 한 번에 수집합니다.
    """
    profile = UserProfile(
        age_group=age_group,
        income_level=income_level,
        employment_status=employment_status
    )
    return profile.dict()

@mcp.tool(
    name="collect_household_profile",
    description="가구 형태 및 특수 상태를 수집합니다."
)
def collect_household_profile(
    current_profile: dict,
    household_type: Literal[
        "SINGLE",
        "PARENT_CHILD",
        "COUPLE",
        "SINGLE_PARENT",
        "OTHER"
    ],
    special_status: List[
        Literal[
            "DISABLED",
            "MULTICULTURAL",
            "VETERAN",
            "NONE"
        ]
    ]
) -> dict:
    """
    가구 형태 및 특수 상태를 추가로 수집합니다.
    필요 시에만 호출되는 조건부 Tool입니다.
    """
    profile = UserProfile(**current_profile)

    profile.household_type = household_type

    if "NONE" in special_status:
        profile.special_status = []
    else:
        profile.special_status = special_status

    return profile.dict()

@mcp.tool(
    name="collect_asset_profile",
    description="재산 보유 여부를 수집합니다."
)
def collect_asset_profile(
    current_profile: dict,
    has_real_estate: bool,
    has_vehicle: bool
) -> dict:
    """
    재산 보유 여부를 수집합니다.
    금액이 아닌 '존재 여부'만 판단합니다.
    """
    profile = UserProfile(**current_profile)

    profile.assets = {
        "has_real_estate": has_real_estate,
        "has_vehicle": has_vehicle
    }

    return profile.dict()
