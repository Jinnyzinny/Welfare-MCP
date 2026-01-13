from typing import Optional, List, Literal, Dict
from pydantic import BaseModel

class UserProfile(BaseModel):
    # 연령대를 3가지로 구분
    age_group: Optional[Literal["YOUTH", "ADULT", "SENIOR"]] = None

    # 소득 수준을 5단계로 구분
    income_level: Optional[
        Literal[
            "BELOW_MEDIAN_50",
            "MEDIAN_50_100",
            "MEDIAN_100_150",
            "ABOVE_MEDIAN_150",
            "UNKNOWN"
        ]
    ] = None
    # 고용 상태를 5가지로 분류
    employment_status: Optional[
        Literal[
            "EMPLOYED",
            "UNEMPLOYED",
            "STUDENT",
            "SELF_EMPLOYED",
            "UNKNOWN"
        ]
    ] = None
    # 가구 형태를 5가지로 구분
    household_type: Optional[
        Literal[
            "SINGLE",
            "PARENT_CHILD",
            "COUPLE",
            "SINGLE_PARENT",
            "OTHER"
        ]
    ] = None

    special_status: Optional[List[str]] = None

    assets: Optional[Dict[str, bool]] = None
