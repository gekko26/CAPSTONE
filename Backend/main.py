from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from models import User, sensorReadings
from database import get_db, engine
from schema import UserCreate, UserResponse, SensorReadingResponse, SendSensorReading
import hashlib
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException

app = FastAPI()



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow all (development)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)





@app.post("/signup")

def create_user(user:UserCreate, db:Session = Depends(get_db)):

    existing_user = db.query(User).filter(User.username == user.username).first()

    if existing_user: 
        raise HTTPException(status_code=400, detail="Username already exist ")


    new_user = User(
        username=user.username,
        hashpassword=user.hashpassword
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "Sign Up Succes"}


@app.post("/sensor", response_model=SensorReadingResponse)
def sensor_data(sensor:SendSensorReading, db:Session = Depends(get_db)):
    
    sensordata = sensorReadings(
        temperature = sensor.temperature,
        alcohol_level = sensor.alcohol_level,
        humidity = sensor.humidity
    )
    db.add(sensordata)
    db.commit()
    db.refresh(sensordata)
    return sensordata


@app.post("/login")
def login(user: UserCreate, db: Session = Depends(get_db)):

    db_user = db.query(User).filter(User.username == user.username).first()

    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid username or password")

    # hashed_pass = hashlib.sha256(user.hashpassword.encode()).hexdigest()
    hashed_pass = user.hashpassword
    
    if db_user.hashpassword != hashed_pass:
        raise HTTPException(status_code=400, detail="Invalid username or password")
        
    return {"message": "Login successful"}
