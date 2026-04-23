from pydantic import BaseModel
from datetime import datetime

# For user registration
class UserCreate(BaseModel):
    username: str
    hashpassword: str

# For response (don’t return password!)
class UserResponse(BaseModel):
    id: int
    username: str
    hashpassword: str
    class Config:
        orm_mode = True  # Allows ORM objects to work directly

class SendSensorReading(BaseModel):
    temperature: float
    alcohol_level: float
    humidity: float
    class Config:
        orm_mode = True

class SensorReadingResponse(BaseModel):
    id: int
    date: datetime
    temperature: float
    alcohol_level: float
    humidity: float

