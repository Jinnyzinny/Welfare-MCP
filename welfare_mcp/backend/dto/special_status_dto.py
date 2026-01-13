from pydantic import BaseModel
from typing import Optional, Literal

class special_status_dto(BaseModel):
    # 특수 신분 여부
    special_status: Optional[
        Literal[
            "VETERAN",
            "DISABLED",
            "NONE"
        ]
    ] = None