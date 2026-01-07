from typing import List
from pydantic import BaseModel

class EligibilityResult(BaseModel):
    service_id: str
    eligible: bool
    reasons: List[str]
    missing_conditions: List[str]