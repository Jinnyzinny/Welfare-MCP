from typing import Optional, List, Literal, Dict
from pydantic import BaseModel

class UserProfile(BaseModel):
    age_group: Optional[Literal["YOUTH", "ADULT", "SENIOR"]] = None

    income_level: Optional[
        Literal[
            "BELOW_MEDIAN_50",
            "MEDIAN_50_100",
            "MEDIAN_100_150",
            "ABOVE_MEDIAN_150",
            "UNKNOWN"
        ]
    ] = None

    employment_status: Optional[
        Literal[
            "EMPLOYED",
            "UNEMPLOYED",
            "STUDENT",
            "SELF_EMPLOYED",
            "UNKNOWN"
        ]
    ] = None

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
