from pydantic import BaseModel
from typing import Optional, Literal

class house_hold_dto(BaseModel):
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