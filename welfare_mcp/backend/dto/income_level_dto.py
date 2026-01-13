from pydantic import BaseModel
from typing import Optional, Literal

class income_level_dto(BaseModel):
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