from typing import Optional

from pydantic import BaseModel

class GECRequest(BaseModel):
  sentences: list[str]
  iteration_count: int = 3
  user_id: Optional[str] = None
  
class HistoryRequest(BaseModel):
  user_id: str
  
class SignInRequest(BaseModel):
  email: str

class SignUpRequest(BaseModel):
  email: str
  username: str

class SaveRequest(BaseModel):
  user_id: str
  correction_id: list[str]

class SavedRequest(BaseModel):
  user_id: str