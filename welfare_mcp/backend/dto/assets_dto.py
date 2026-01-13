from pydantic import BaseModel
from typing import Optional, Dict

class assets_dto(BaseModel):
    assets: Optional[Dict[str, bool]] = None