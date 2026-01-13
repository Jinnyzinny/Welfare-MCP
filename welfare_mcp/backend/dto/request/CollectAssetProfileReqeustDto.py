from pydantic import BaseModel
from backend.entity.UserProfile import UserProfile

class CollectAssetProfileRequestDto(BaseModel):
    current_profile: UserProfile
    has_real_estate: bool
    has_vehicle: bool