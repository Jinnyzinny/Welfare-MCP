from pydantic import BaseModel
from typing import Optional, Literal

class employment_status_dto(BaseModel):
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