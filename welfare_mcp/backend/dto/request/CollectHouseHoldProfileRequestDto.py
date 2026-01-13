from pydantic import BaseModel
from typing import Literal, List, Optional

class CurrentProfile(BaseModel):
    age_group: Optional[Literal["YOUTH", "ADULT", "SENIOR"]] = None
    income_level: Optional[Literal[
        "BELOW_MEDIAN_50",
        "MEDIAN_50_100",
        "MEDIAN_100_150",
        "ABOVE_MEDIAN_150",
        "UNKNOWN"
    ]] = None
    employment_status: Optional[Literal[
        "EMPLOYED",
        "UNEMPLOYED",
        "STUDENT",
        "SELF_EMPLOYED",
        "UNKNOWN"
    ]] = None

class CollectHouseHoldProfileRequestDto(BaseModel):
    current_profile: CurrentProfile
    household_type: Literal[
        "SINGLE",
        "PARENT_CHILD",
        "COUPLE",
        "SINGLE_PARENT",
        "OTHER"
    ]
    special_status: List[
        Literal[
            "DISABLED",
            "MULTICULTURAL",
            "VETERAN",
            "NONE"
        ]
    ]