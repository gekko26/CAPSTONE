from sqlalchemy import Column, Integer, String, TIMESTAMP, Float
from database import BASE  # import your declarative_base from database.py
from sqlalchemy.sql import func

class User(BASE):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashpassword = Column(String(100), nullable=False)

class sensorReadings(BASE):
    __tablename__ = "sensor_readings"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(TIMESTAMP,server_default=func.now(), index=True)
    alcohol_level = Column(Float, nullable=False )
    temperature = Column(Float, nullable=False )
    humidity = Column(Float, nullable=False )