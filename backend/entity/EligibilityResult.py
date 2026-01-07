from typing import List, Literal
from pydantic import BaseModel

class EligibilityResult(BaseModel):
    service_id: str
    eligible: bool
    reasons: List[str]
    missing_conditions: List[str]
